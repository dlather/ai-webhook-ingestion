"""Tests for health and ingestion lookup endpoints."""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedParameter=false

import uuid
import pytest
import httpx
from httpx import ASGITransport
from fastapi import FastAPI

from src.db import create_engine, create_session_factory, init_db
from src.models import RawEvent
from src.worker.queue import EventQueue
from src.api import health
from src.api.deps import set_session_factory, set_event_queue


@pytest.fixture
async def setup():
    engine = create_engine("sqlite+aiosqlite://")
    await init_db(engine)
    sf = create_session_factory(engine)
    q = EventQueue(maxsize=10)
    set_session_factory(sf)
    set_event_queue(q)
    yield {"session_factory": sf, "queue": q}
    await engine.dispose()


@pytest.fixture
async def test_app(setup):
    app = FastAPI()
    app.include_router(health.router)
    return app


@pytest.fixture
async def client(test_app):
    async with httpx.AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        yield c


class TestHealthEndpoint:
    async def test_health_returns_200(self, client, setup):
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_health_response_shape(self, client, setup):
        resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["db"] == "connected"
        assert "queue_depth" in data
        assert isinstance(data["queue_depth"], int)

    async def test_health_shows_queue_depth(self, client, setup):
        q = setup["queue"]
        await q.put("test-event-1")
        await q.put("test-event-2")
        resp = await client.get("/health")
        data = resp.json()
        assert data["queue_depth"] == 2


class TestIngestionLookup:
    async def test_returns_404_for_unknown(self, client, setup):
        resp = await client.get("/ingestions/ing_nonexistent")
        assert resp.status_code == 404

    async def test_returns_200_for_known_event(self, client, setup):
        sf = setup["session_factory"]
        raw_id = str(uuid.uuid4())
        async with sf() as session:
            session.add(
                RawEvent(
                    id=raw_id,
                    ingestion_id="ing_known123",
                    vendor="test-vendor",
                    weak_payload_hash="testhash",
                    content_type="application/json",
                    raw_payload_json={"test": 1},
                    status="RECEIVED",
                )
            )
            await session.commit()

        resp = await client.get("/ingestions/ing_known123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingestion_id"] == "ing_known123"
        assert data["vendor"] == "test-vendor"
        assert data["status"] == "RECEIVED"

    async def test_ingestion_response_shape(self, client, setup):
        sf = setup["session_factory"]
        raw_id = str(uuid.uuid4())
        async with sf() as session:
            session.add(
                RawEvent(
                    id=raw_id,
                    ingestion_id="ing_shape_test",
                    vendor="acme",
                    weak_payload_hash="shapehash",
                    content_type="application/json",
                    raw_payload_json={},
                    status="COMPLETED",
                )
            )
            await session.commit()

        resp = await client.get("/ingestions/ing_shape_test")
        data = resp.json()
        assert "ingestion_id" in data
        assert "vendor" in data
        assert "status" in data
        assert "received_at" in data
