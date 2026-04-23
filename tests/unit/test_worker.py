# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db import create_engine, create_session_factory, init_db
from src.models import OutboxEvent, RawEvent
from src.worker.processor import EventProcessor
from src.worker.queue import EventQueue
from src.worker.relay import OutboxRelay


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    eng = create_engine("sqlite+aiosqlite://")
    await init_db(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


class TestEventQueue:
    async def test_put_and_get(self):
        q = EventQueue(maxsize=10)
        await q.put("event-1")
        item = await q.get()
        q.task_done()
        assert item == "event-1"

    async def test_depth_reflects_size(self):
        q = EventQueue(maxsize=10)
        await q.put("evt-a")
        await q.put("evt-b")
        assert q.depth() == 2

    async def test_depth_decreases_after_get(self):
        q = EventQueue(maxsize=10)
        await q.put("evt-x")
        _ = await q.get()
        q.task_done()
        assert q.depth() == 0

    async def test_shutdown_sentinel_stops_consumer(self):
        q = EventQueue(maxsize=10)
        await q.put("event-1")
        await q.shutdown()

        item1 = await q.get()
        q.task_done()
        assert item1 == "event-1"

        item2 = await q.get()
        if item2 is not None:
            q.task_done()
        assert item2 is None

    async def test_empty_queue_depth_is_zero(self):
        q = EventQueue(maxsize=5)
        assert q.depth() == 0


class TestOutboxRelay:
    async def test_relay_dispatches_pending_event(self, session_factory):
        outbox_id = str(uuid.uuid4())
        raw_id = str(uuid.uuid4())

        async with session_factory() as session:
            session.add(
                RawEvent(
                    id=raw_id,
                    ingestion_id="ing_relay",
                    vendor="acme",
                    weak_payload_hash="h1",
                    content_type="application/json",
                    raw_payload_json={},
                    status="QUEUED",
                )
            )
            session.add(
                OutboxEvent(
                    id=outbox_id,
                    aggregate_type="raw_event",
                    aggregate_id=raw_id,
                    event_type="webhook.received",
                    payload_json={},
                    status="PENDING",
                )
            )
            await session.commit()

        q = EventQueue(maxsize=10)
        relay = OutboxRelay(session_factory, q, poll_interval=0.05)

        task = asyncio.create_task(relay.start())
        await asyncio.sleep(0.3)
        _ = task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert q.depth() >= 1

        async with session_factory() as session:
            outbox = await session.get(OutboxEvent, outbox_id)
            assert outbox.status == "DISPATCHED"

    async def test_recover_stale_resets_dispatched_rows(self, session_factory):
        stale_id = str(uuid.uuid4())
        raw_id = str(uuid.uuid4())
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)

        async with session_factory() as session:
            session.add(
                RawEvent(
                    id=raw_id,
                    ingestion_id="ing_stale",
                    vendor="acme",
                    weak_payload_hash="h2",
                    content_type="application/json",
                    raw_payload_json={},
                    status="PROCESSING",
                )
            )
            session.add(
                OutboxEvent(
                    id=stale_id,
                    aggregate_type="raw_event",
                    aggregate_id=raw_id,
                    event_type="webhook.received",
                    payload_json={},
                    status="DISPATCHED",
                    processing_started_at=stale_time,
                )
            )
            await session.commit()

        q = EventQueue(maxsize=10)
        relay = OutboxRelay(session_factory, q, poll_interval=0.05)
        await relay.recover_stale()

        async with session_factory() as session:
            outbox = await session.get(OutboxEvent, stale_id)
            assert outbox.status == "PENDING"

    async def test_relay_does_not_dispatch_non_pending(self, session_factory):
        outbox_id = str(uuid.uuid4())
        raw_id = str(uuid.uuid4())

        async with session_factory() as session:
            session.add(
                RawEvent(
                    id=raw_id,
                    ingestion_id="ing_done",
                    vendor="acme",
                    weak_payload_hash="h3",
                    content_type="application/json",
                    raw_payload_json={},
                    status="COMPLETED",
                )
            )
            session.add(
                OutboxEvent(
                    id=outbox_id,
                    aggregate_type="raw_event",
                    aggregate_id=raw_id,
                    event_type="webhook.received",
                    payload_json={},
                    status="DISPATCHED",
                )
            )
            await session.commit()

        q = EventQueue(maxsize=10)
        relay = OutboxRelay(session_factory, q, poll_interval=0.05)

        task = asyncio.create_task(relay.start())
        await asyncio.sleep(0.2)
        _ = task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert q.depth() == 0


class _PipelineSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process(self, raw_event_id: str) -> None:
        self.calls.append(raw_event_id)


class _FailingPipeline:
    async def process(self, raw_event_id: str) -> None:
        raise RuntimeError(f"boom:{raw_event_id}")


class TestEventProcessor:
    async def test_processor_invokes_pipeline_for_event(self, session_factory):
        q = EventQueue(maxsize=10)
        pipeline = _PipelineSpy()
        processor = EventProcessor(session_factory, q, pipeline)

        task = asyncio.create_task(processor.start())
        await q.put("raw-1")
        await asyncio.sleep(0.05)
        await q.shutdown()
        await task

        assert pipeline.calls == ["raw-1"]

    async def test_processor_stops_on_shutdown_sentinel(self, session_factory):
        q = EventQueue(maxsize=10)
        pipeline = _PipelineSpy()
        processor = EventProcessor(session_factory, q, pipeline)

        task = asyncio.create_task(processor.start())
        await q.shutdown()
        await asyncio.wait_for(task, timeout=1)

        assert pipeline.calls == []

    async def test_processor_calls_task_done_even_on_pipeline_error(self, session_factory):
        q = EventQueue(maxsize=10)
        processor = EventProcessor(session_factory, q, _FailingPipeline())

        task = asyncio.create_task(processor.start())
        await q.put("raw-fail")
        await asyncio.sleep(0.05)
        await asyncio.wait_for(q.join(), timeout=1)
        await q.shutdown()
        await asyncio.wait_for(task, timeout=1)

        assert q.depth() == 0
