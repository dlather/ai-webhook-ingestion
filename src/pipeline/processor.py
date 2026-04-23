# pyright: reportUnknownMemberType=false

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.normalized_record import NormalizedRecord
from src.models.processing_attempt import ProcessingAttempt
from src.models.raw_event import RawEvent
from src.schemas.events import EventType
from src.services.llm.protocol import LLMService
from src.services.quarantine import QuarantineReasonCode, QuarantineService
from src.services.schema_registry import SchemaRegistry

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    status: str
    event_type: EventType | None = None
    normalized_record_id: str | None = None
    quarantine_id: str | None = None
    error: str | None = None


class ProcessingPipeline:
    """Orchestrates the two-pass LLM classification and extraction pipeline."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm_service: LLMService,
        schema_registry: SchemaRegistry,
        confidence_threshold: float = 0.7,
    ) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] = session_factory
        self._llm: LLMService = llm_service
        self._registry: SchemaRegistry = schema_registry
        self._threshold: float = confidence_threshold

    async def process(self, raw_event_id: str) -> ProcessingResult:
        """Process a single raw event through the full pipeline."""
        try:
            return await self._process_inner(raw_event_id)
        except Exception as exc:
            logger.error("Pipeline fatal error for %s: %s", raw_event_id, exc, exc_info=True)
            await self._update_raw_event_status(raw_event_id, "FAILED_TERMINAL")
            return ProcessingResult(status="FAILED_TERMINAL", error=str(exc))

    _TERMINAL_STATUSES: set[str] = {"COMPLETED", "QUARANTINED", "FAILED_TERMINAL"}

    async def _process_inner(self, raw_event_id: str) -> ProcessingResult:
        async with self._session_factory() as session:
            raw_event = await session.get(RawEvent, raw_event_id)
            if raw_event is None:
                return ProcessingResult(status="FAILED_TERMINAL", error="Raw event not found")
            # Idempotency guard: skip if already in a terminal state (e.g. relay re-dispatch)
            if raw_event.status in self._TERMINAL_STATUSES:
                logger.debug(
                    "Skipping already-terminal event %s (status=%s)", raw_event_id, raw_event.status
                )
                return ProcessingResult(status=raw_event.status)
            payload: dict[str, object] = raw_event.raw_payload_json

        await self._update_raw_event_status(raw_event_id, "PROCESSING")

        classification = await self._llm.classify(payload)
        await self._record_attempt(raw_event_id, "CLASSIFY", "SUCCESS")
        event_type = classification.event_type
        confidence = classification.confidence

        if event_type == EventType.UNCLASSIFIED:
            await self._update_raw_event_status(raw_event_id, "COMPLETED")
            return ProcessingResult(status="COMPLETED", event_type=event_type)

        if confidence < self._threshold:
            async with self._session_factory() as session:
                svc = QuarantineService(session)
                quarantine_event = await svc.quarantine(
                    raw_event_id,
                    QuarantineReasonCode.LOW_CONFIDENCE,
                    f"Confidence {confidence:.2f} below threshold {self._threshold:.2f}",
                )
            return ProcessingResult(
                status="QUARANTINED",
                event_type=event_type,
                quarantine_id=quarantine_event.id,
            )

        entry = self._registry.get(event_type)
        if entry is None:
            async with self._session_factory() as session:
                svc = QuarantineService(session)
                quarantine_event = await svc.quarantine(
                    raw_event_id,
                    QuarantineReasonCode.UNKNOWN_TYPE,
                    f"No schema for {event_type}",
                )
            return ProcessingResult(
                status="QUARANTINED",
                event_type=event_type,
                quarantine_id=quarantine_event.id,
            )

        extracted: BaseModel = await self._llm.extract(payload, event_type, entry.schema_class)
        await self._record_attempt(raw_event_id, "EXTRACT", "SUCCESS")

        normalized_record_id = str(uuid.uuid4())
        async with self._session_factory() as session:
            session.add(
                NormalizedRecord(
                    id=normalized_record_id,
                    raw_event_id=raw_event_id,
                    record_type=event_type.value,
                    schema_version=entry.version,
                    normalized_payload_json=extracted.model_dump(mode="json"),
                    confidence_score=confidence,
                    review_flag=False,
                )
            )
            await session.commit()

        await self._update_raw_event_status(raw_event_id, "COMPLETED")
        return ProcessingResult(
            status="COMPLETED",
            event_type=event_type,
            normalized_record_id=normalized_record_id,
        )

    async def _update_raw_event_status(self, raw_event_id: str, status: str) -> None:
        async with self._session_factory() as session:
            raw_event = await session.get(RawEvent, raw_event_id)
            if raw_event is None:
                return
            raw_event.status = status
            await session.commit()

    async def _record_attempt(
        self,
        raw_event_id: str,
        stage: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            session.add(
                ProcessingAttempt(
                    id=str(uuid.uuid4()),
                    raw_event_id=raw_event_id,
                    stage=stage,
                    attempt_no=1,
                    status=status,
                    error_message=error,
                    started_at=now,
                    finished_at=now,
                )
            )
            await session.commit()
