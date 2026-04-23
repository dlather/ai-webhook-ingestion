from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.db import create_engine, create_session_factory, init_db


class TestCreateEngine:
    def test_returns_async_engine(self):
        engine = create_engine("sqlite+aiosqlite://")
        assert isinstance(engine, AsyncEngine)

    def test_sqlite_engine_created(self):
        engine = create_engine("sqlite+aiosqlite://")
        assert "sqlite" in str(engine.url)

    async def test_engine_connects(self):
        engine = create_engine("sqlite+aiosqlite://")
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
        await engine.dispose()


class TestWALMode:
    async def test_wal_mode_enabled_for_file_db(self, tmp_path: Path):
        """WAL mode is enabled on file-based SQLite databases."""
        db_path = tmp_path / "test.db"
        engine = create_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            mode = result.scalar()
            assert mode == "wal"
        await engine.dispose()

    def test_wal_not_applied_to_memory_db(self):
        """In-memory SQLite doesn't need WAL — engine still created fine."""
        engine = create_engine("sqlite+aiosqlite://")
        assert engine is not None


class TestSessionFactory:
    def test_returns_sessionmaker(self):
        engine = create_engine("sqlite+aiosqlite://")
        factory = create_session_factory(engine)
        assert callable(factory)

    async def test_session_usable(self):
        engine = create_engine("sqlite+aiosqlite://")
        factory = create_session_factory(engine)
        async with factory() as session:
            assert isinstance(session, AsyncSession)
        await engine.dispose()


class TestInitDb:
    async def test_creates_all_tables(self):
        engine = create_engine("sqlite+aiosqlite://")
        await init_db(engine)
        async with engine.connect() as conn:
            result = await conn.run_sync(
                lambda c: c.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            )
            table_names = {row[0] for row in result}
        await engine.dispose()

        assert "raw_events" in table_names
        assert "outbox_events" in table_names
        assert "processing_attempts" in table_names
        assert "normalized_records" in table_names
        assert "quarantine_events" in table_names

    async def test_idempotent(self):
        """Calling init_db twice doesn't raise."""
        engine = create_engine("sqlite+aiosqlite://")
        await init_db(engine)
        await init_db(engine)
        await engine.dispose()
