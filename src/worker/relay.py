# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.outbox_event import OutboxEvent
from .queue import EventQueue

logger = logging.getLogger(__name__)

_STALE_THRESHOLD_MINUTES = 5
_BATCH_SIZE = 50


class OutboxRelay:
    """Poll the outbox table and dispatch pending events to the EventQueue."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_queue: EventQueue,
        poll_interval: float = 2.0,
    ) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] = session_factory
        self._queue: EventQueue = event_queue
        self._poll_interval: float = poll_interval

    async def recover_stale(self) -> None:
        """Reset stale outbox rows stuck in DISPATCHED back to PENDING."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_THRESHOLD_MINUTES)

        async with self._session_factory() as session:
            result = await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.status == "DISPATCHED",
                    OutboxEvent.processing_started_at <= cutoff,
                )
            )
            stale_rows = result.scalars().all()

            for row in stale_rows:
                row.status = "PENDING"
                row.processing_started_at = None
                logger.warning("Recovered stale DISPATCHED event: %s", row.id)

            if stale_rows:
                await session.commit()

    async def start(self) -> None:
        """Run stale recovery once, then poll and dispatch forever."""
        await self.recover_stale()

        while True:
            try:
                await self._poll_and_dispatch()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Relay error")

            await asyncio.sleep(self._poll_interval)

    async def _poll_and_dispatch(self) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.status == "PENDING")
                .order_by(OutboxEvent.created_at)
                .limit(_BATCH_SIZE)
            )
            rows = result.scalars().all()

            for row in rows:
                row.status = "DISPATCHED"
                row.processing_started_at = datetime.now(timezone.utc)
                await self._queue.put(row.aggregate_id)
                logger.debug("Dispatched outbox event: raw_event_id=%s", row.aggregate_id)

            if rows:
                await session.commit()
