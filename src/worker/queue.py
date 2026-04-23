import asyncio
import logging

logger = logging.getLogger(__name__)

_SENTINEL = None


class EventQueue:
    """Thin wrapper around asyncio.Queue for event IDs.

    The queue is a notification channel only — event IDs are enqueued to signal
    the processor. The DB outbox table is the durability layer.
    """

    def __init__(self, maxsize: int = 100) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=maxsize)

    async def put(self, event_id: str) -> None:
        await self._queue.put(event_id)

    async def get(self) -> str | None:
        """Return an event ID or None shutdown sentinel."""
        return await self._queue.get()

    def task_done(self) -> None:
        self._queue.task_done()

    def depth(self) -> int:
        return self._queue.qsize()

    async def join(self) -> None:
        await self._queue.join()

    async def shutdown(self) -> None:
        """Signal consumers to stop by enqueueing the sentinel."""
        logger.info("Shutting down event queue")
        await self._queue.put(_SENTINEL)
