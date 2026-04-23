import logging
import sqlite3

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine."""
    is_sqlite = "sqlite" in database_url
    is_memory = database_url.endswith("//") or ":memory:" in database_url

    if is_sqlite:
        engine = create_async_engine(
            database_url,
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 120},
        )

        if not is_memory:

            @event.listens_for(engine.sync_engine, "connect")
            def set_wal_mode(
                dbapi_connection: sqlite3.Connection, _connection_record: object
            ) -> None:
                _ = dbapi_connection.execute("PRAGMA journal_mode=WAL")
                logger.debug("SQLite WAL mode enabled")

            _ = set_wal_mode

        return engine

    return create_async_engine(database_url, echo=False)
