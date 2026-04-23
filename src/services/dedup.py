import hashlib
import json
import logging

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.raw_event import RawEvent

logger = logging.getLogger(__name__)


def canonicalize_json(payload: Mapping[str, object]) -> str:
    """Return deterministic JSON string with sorted keys (recursively)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_payload_hash(canonical_json: str) -> str:
    """Return SHA-256 hex digest of the canonical JSON string."""
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def derive_strong_dedupe_key(vendor: str, vendor_event_id: str | None) -> str | None:
    """Return a strong dedup key if vendor event ID is present."""
    if not vendor_event_id:
        return None
    return f"{vendor}:{vendor_event_id}"


def derive_weak_dedupe_key(vendor: str, payload: Mapping[str, object]) -> str:
    """Return a weak dedup key based on vendor + payload hash."""
    canonical = canonicalize_json(payload)
    payload_hash = compute_payload_hash(canonical)
    return f"{vendor}:{payload_hash}"


class DeduplicationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    async def check_duplicate(
        self,
        vendor: str,
        vendor_event_id: str | None,
        payload: Mapping[str, object],
    ) -> tuple[bool, str | None]:
        """Check if an event is a duplicate. Returns (is_duplicate, existing_ingestion_id)."""
        strong_key = derive_strong_dedupe_key(vendor, vendor_event_id)
        if strong_key:
            result = await self._session.execute(
                select(RawEvent.ingestion_id).where(RawEvent.strong_dedupe_key == strong_key)
            )
            row = result.scalar_one_or_none()
            if row:
                logger.info("Strong duplicate detected: %s", strong_key)
                return True, row

        canonical = canonicalize_json(payload)
        weak_hash = compute_payload_hash(canonical)
        result = await self._session.execute(
            select(RawEvent.ingestion_id).where(
                RawEvent.vendor == vendor,
                RawEvent.weak_payload_hash == weak_hash,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            logger.info("Weak duplicate detected for vendor=%s", vendor)
            return True, row

        return False, None
