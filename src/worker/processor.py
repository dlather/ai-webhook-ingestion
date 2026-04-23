# pyright: reportAny=false, reportExplicitAny=false, reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .queue import EventQueue

logger = logging.getLogger(__name__)


class EventProcessor:
    """Consume event IDs from the queue and invoke the processing pipeline."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        event_queue: EventQueue,
        pipeline: Any,
    ) -> None:
        self._session_factory: async_sessionmaker[AsyncSession] = session_factory
        self._queue: EventQueue = event_queue
        self._pipeline: Any = pipeline

    async def start(self) -> None:
        """Get event IDs from the queue until the shutdown sentinel arrives."""
        while True:
            raw_event_id = await self._queue.get()
            try:
                if raw_event_id is None:
                    logger.info("EventProcessor received shutdown signal")
                    return

                await self._process_one(raw_event_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Processor error for event %s", raw_event_id)
            finally:
                self._queue.task_done()

    async def _process_one(self, raw_event_id: str) -> None:
        try:
            await self._pipeline.process(raw_event_id)
        except Exception:
            logger.exception("Pipeline failed for event %s", raw_event_id)
