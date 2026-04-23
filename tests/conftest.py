import pytest
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """In-memory SQLite engine for tests."""
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Async DB session."""
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        yield session
