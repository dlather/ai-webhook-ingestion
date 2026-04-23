"""Health endpoints."""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from src.api.deps import get_event_queue, get_session_factory
from src.models.normalized_record import NormalizedRecord
from src.models.quarantine_event import QuarantineEvent
from src.models.raw_event import RawEvent

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check() -> JSONResponse:
    """Check DB connectivity and queue depth."""
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            _ = await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error(f"Health check DB error: {exc}")
        return JSONResponse(
            {"status": "unhealthy", "db": "disconnected", "error": str(exc)},
            status_code=503,
        )

    event_queue = get_event_queue()
    return JSONResponse(
        {
            "status": "healthy",
            "db": "connected",
            "queue_depth": event_queue.depth(),
        }
    )


@router.get("/ingestions/{ingestion_id}")
async def get_ingestion(ingestion_id: str) -> JSONResponse:
    """Look up an ingestion by its external-facing ID."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(RawEvent).where(RawEvent.ingestion_id == ingestion_id)
        )
        raw = result.scalar_one_or_none()

    if raw is None:
        return JSONResponse({"error": "Ingestion not found"}, status_code=404)

    response = {
        "ingestion_id": raw.ingestion_id,
        "vendor": raw.vendor,
        "status": raw.status,
        "received_at": raw.received_at.isoformat() if raw.received_at else None,
    }

    if raw.status == "COMPLETED":
        async with session_factory() as enrich_session:
            nr_result = await enrich_session.execute(
                select(NormalizedRecord).where(NormalizedRecord.raw_event_id == raw.id)
            )
            nr = nr_result.scalar_one_or_none()
            if nr:
                response["record_type"] = nr.record_type
    elif raw.status == "QUARANTINED":
        async with session_factory() as enrich_session:
            q_result = await enrich_session.execute(
                select(QuarantineEvent).where(QuarantineEvent.raw_event_id == raw.id)
            )
            q = q_result.scalar_one_or_none()
            if q:
                response["reason_code"] = q.reason_code

    return JSONResponse(response)
