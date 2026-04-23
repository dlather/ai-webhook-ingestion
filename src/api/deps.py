from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.worker.queue import EventQueue

_session_factory: async_sessionmaker[AsyncSession] | None = None
_event_queue: EventQueue | None = None


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    global _session_factory
    _session_factory = factory


def set_event_queue(queue: EventQueue) -> None:
    global _event_queue
    _event_queue = queue


def reset_dependencies() -> None:
    global _session_factory, _event_queue
    _session_factory = None
    _event_queue = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError(
            "Session factory not initialized — call set_session_factory() at startup"
        )
    return _session_factory


def get_event_queue() -> EventQueue:
    if _event_queue is None:
        raise RuntimeError("Event queue not initialized — call set_event_queue() at startup")
    return _event_queue
