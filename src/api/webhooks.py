import json
import logging
import uuid
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models.outbox_event import OutboxEvent
from src.models.raw_event import RawEvent
from src.services.dedup import (
    canonicalize_json,
    compute_payload_hash,
    derive_strong_dedupe_key,
    DeduplicationService,
)
from src.api.deps import get_session_factory, get_event_queue

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhooks/{vendor}")
async def ingest_webhook(vendor: str, request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type:
        return JSONResponse({"error": "Content-Type must be application/json"}, status_code=415)

    from src.config import get_settings

    settings = get_settings()
    body = await request.body()
    if len(body) > settings.MAX_PAYLOAD_SIZE_BYTES:
        return JSONResponse({"error": "Payload too large"}, status_code=413)

    try:
        payload = cast(dict[str, object], json.loads(body))
    except json.JSONDecodeError as exc:
        return JSONResponse({"error": f"Invalid JSON: {exc}"}, status_code=400)

    vendor_event_id = request.headers.get("X-Event-ID")

    session_factory = get_session_factory()
    async with session_factory() as session:
        dedup_svc = DeduplicationService(session)
        is_dup, existing_id = await dedup_svc.check_duplicate(vendor, vendor_event_id, payload)

    if is_dup:
        return JSONResponse({"ingestion_id": existing_id, "status": "duplicate"}, status_code=200)

    event_id = str(uuid.uuid4())
    ingestion_id = f"ing_{uuid.uuid4().hex[:12]}"

    strong_key = derive_strong_dedupe_key(vendor, vendor_event_id)
    canonical = canonicalize_json(payload)
    weak_hash = compute_payload_hash(canonical)

    headers_to_store = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("authorization", "cookie", "x-api-key")
    }

    try:
        async with session_factory() as session:
            raw_event = RawEvent(
                id=event_id,
                ingestion_id=ingestion_id,
                vendor=vendor,
                vendor_event_id=vendor_event_id,
                strong_dedupe_key=strong_key,
                weak_payload_hash=weak_hash,
                content_type=content_type,
                headers_json=headers_to_store,
                raw_payload_json=payload,
                status="RECEIVED",
            )
            outbox_event = OutboxEvent(
                id=str(uuid.uuid4()),
                aggregate_type="raw_event",
                aggregate_id=event_id,
                event_type="webhook.received",
                payload_json={"ingestion_id": ingestion_id},
                status="PENDING",
            )
            session.add(raw_event)
            session.add(outbox_event)
            await session.commit()
    except IntegrityError:
        async with session_factory() as session:
            result = await session.execute(
                select(RawEvent.ingestion_id).where(
                    RawEvent.vendor == vendor,
                    RawEvent.weak_payload_hash == weak_hash,
                )
            )
            existing_id = result.scalar_one_or_none()
        return JSONResponse({"ingestion_id": existing_id, "status": "duplicate"}, status_code=200)

    event_queue = get_event_queue()
    await event_queue.put(event_id)

    logger.info(f"Webhook accepted: ingestion_id={ingestion_id}, vendor={vendor}")
    return JSONResponse({"ingestion_id": ingestion_id, "status": "accepted"}, status_code=202)
