# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportMissingParameterType=false, reportUnusedCallResult=false

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import asyncio
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api import health, webhooks
from src.api.deps import reset_dependencies, set_event_queue, set_session_factory
from src.db import create_engine, create_session_factory, init_db
from src.models import RawEvent
from src.pipeline.processor import ProcessingPipeline
from src.services.llm.mock import MockLLMService
from src.services.schema_registry import create_default_registry
from src.worker.processor import EventProcessor
from src.worker.queue import EventQueue
from src.worker.relay import OutboxRelay

AppStack = tuple[FastAPI, async_sessionmaker[AsyncSession], EventQueue]
WaitForStatusFn = Callable[
    [async_sessionmaker[AsyncSession], str, list[str], float],
    Awaitable[str | None],
]


@asynccontextmanager
async def create_test_app_stack(
    confidence_threshold: float = 0.7,
    db_url: str = "sqlite+aiosqlite://",
) -> AsyncGenerator[AppStack, None]:
    """Create a real FastAPI app wired to SQLite and mock workers."""
    engine = create_engine(db_url)
    await init_db(engine)
    session_factory = create_session_factory(engine)

    llm_service = MockLLMService(delay_seconds=0.0, seed=42)
    registry = create_default_registry()
    event_queue = EventQueue(maxsize=100)
    relay = OutboxRelay(session_factory, event_queue, poll_interval=0.5)
    pipeline = ProcessingPipeline(
        session_factory=session_factory,
        llm_service=llm_service,
        schema_registry=registry,
        confidence_threshold=confidence_threshold,
    )
    processor = EventProcessor(session_factory, event_queue, pipeline)

    set_session_factory(session_factory)
    set_event_queue(event_queue)

    await relay.recover_stale()
    relay_task = asyncio.create_task(relay.start(), name="test-relay")
    processor_task = asyncio.create_task(processor.start(), name="test-processor")

    app = FastAPI()
    app.include_router(webhooks.router)
    app.include_router(health.router)

    try:
        yield app, session_factory, event_queue
    finally:
        await event_queue.shutdown()
        relay_task.cancel()
        processor_task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(relay_task, processor_task, return_exceptions=True),
                timeout=2.0,
            )
        except asyncio.TimeoutError:
            pass
        reset_dependencies()
        await engine.dispose()


async def wait_for_status(
    session_factory: async_sessionmaker[AsyncSession],
    ingestion_id: str,
    target_statuses: list[str],
    timeout: float = 3.0,
) -> str | None:
    """Poll DB until raw event reaches a target status or timeout expires."""
    deadline = asyncio.get_running_loop().time() + timeout
    status: str | None = None

    while asyncio.get_running_loop().time() < deadline:
        async with session_factory() as session:
            result = await session.execute(
                select(RawEvent.status).where(RawEvent.ingestion_id == ingestion_id)
            )
            status = result.scalar_one_or_none()
            if status in target_statuses:
                return status
        await asyncio.sleep(0.1)

    return status


@pytest.fixture
def app_factory() -> Callable[[float], AbstractAsyncContextManager[AppStack]]:
    return create_test_app_stack


@pytest.fixture
def wait_for_status_fn() -> WaitForStatusFn:
    return wait_for_status


@pytest.fixture
async def app_with_workers(tmp_path: pytest.TempPathFactory) -> AsyncGenerator[AppStack, None]:
    db_path = tmp_path / "test_e2e.db"
    async with create_test_app_stack(db_url=f"sqlite+aiosqlite:///{db_path}") as app_stack:
        yield app_stack


@pytest.fixture
async def client(
    app_with_workers: AppStack,
) -> AsyncGenerator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession], EventQueue], None]:
    app, session_factory, event_queue = app_with_workers
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as async_client:
        yield async_client, session_factory, event_queue
