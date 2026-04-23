import logging
import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.quarantine_event import QuarantineEvent
from src.models.raw_event import RawEvent

logger = logging.getLogger(__name__)


class QuarantineReasonCode:
    LOW_CONFIDENCE: Final[str] = "LOW_CONFIDENCE"
    VALIDATION_FAILURE: Final[str] = "VALIDATION_FAILURE"
    EXTRACTION_FAILURE: Final[str] = "EXTRACTION_FAILURE"
    LLM_ERROR: Final[str] = "LLM_ERROR"
    UNKNOWN_TYPE: Final[str] = "UNKNOWN_TYPE"


class QuarantineService:
    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    async def quarantine(
        self,
        raw_event_id: str,
        reason_code: str,
        reason_details: str,
        raw_llm_output: dict[str, object] | None = None,
    ) -> QuarantineEvent:
        """Create a quarantine record and update raw event status atomically."""
        quarantine_event = QuarantineEvent(
            id=str(uuid.uuid4()),
            raw_event_id=raw_event_id,
            reason_code=reason_code,
            reason_details=reason_details,
            raw_llm_output_json=raw_llm_output,
            review_status="PENDING",
        )
        self._session.add(quarantine_event)

        raw_event = await self._session.get(RawEvent, raw_event_id)
        if raw_event:
            raw_event.status = "QUARANTINED"

        await self._session.commit()
        logger.info("Event quarantined: raw_event_id=%s, reason=%s", raw_event_id, reason_code)
        return quarantine_event

    async def get_quarantined(self, raw_event_id: str) -> QuarantineEvent | None:
        """Look up a quarantine record by raw event ID."""
        result = await self._session.execute(
            select(QuarantineEvent).where(QuarantineEvent.raw_event_id == raw_event_id)
        )
        return result.scalar_one_or_none()
