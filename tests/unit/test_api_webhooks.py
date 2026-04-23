import uuid
import pytest
import httpx
from httpx import ASGITransport
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.db import create_engine, create_session_factory, init_db
from src.models import RawEvent, OutboxEvent
from src.services.dedup import DeduplicationService
from src.worker.queue import EventQueue
from src.api import webhooks
from src.api.deps import (
    get_session_factory,
    get_event_queue,
    set_session_factory,
    set_event_queue,
)
from sqlalchemy import select


@pytest.fixture
async def setup(tmp_path):
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
    app.include_router(webhooks.router)
    return app


@pytest.fixture
async def client(test_app):
    async with httpx.AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        yield c


class TestWebhookEndpointAccept:
    async def test_valid_json_returns_202(self, client, setup):
        resp = await client.post(
            "/webhooks/acme",
            json={"tracking": "SHIP-001"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "ingestion_id" in data
        assert data["status"] == "accepted"

    async def test_ingestion_id_format(self, client, setup):
        resp = await client.post("/webhooks/acme", json={"test": "data"})
        ing_id = resp.json()["ingestion_id"]
        assert ing_id.startswith("ing_")

    async def test_raw_event_stored_in_db(self, client, setup):
        resp = await client.post(
            "/webhooks/acme",
            json={"tracking": "SHIP-002"},
            headers={"X-Event-ID": "evt-store-test"},
        )
        ing_id = resp.json()["ingestion_id"]
        sf = setup["session_factory"]
        async with sf() as session:
            result = await session.execute(select(RawEvent).where(RawEvent.ingestion_id == ing_id))
            raw = result.scalar_one_or_none()
        assert raw is not None
        assert raw.vendor == "acme"
        assert raw.vendor_event_id == "evt-store-test"

    async def test_outbox_event_created(self, client, setup):
        resp = await client.post("/webhooks/acme", json={"data": "x"})
        ing_id = resp.json()["ingestion_id"]
        sf = setup["session_factory"]
        async with sf() as session:
            raw_result = await session.execute(
                select(RawEvent).where(RawEvent.ingestion_id == ing_id)
            )
            raw = raw_result.scalar_one()
            outbox_result = await session.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == raw.id)
            )
            outbox = outbox_result.scalar_one_or_none()
        assert outbox is not None
        assert outbox.status == "PENDING"


class TestWebhookEndpointValidation:
    async def test_wrong_content_type_returns_415(self, client, setup):
        resp = await client.post(
            "/webhooks/acme", content=b"not-json", headers={"Content-Type": "text/plain"}
        )
        assert resp.status_code == 415

    async def test_malformed_json_returns_400(self, client, setup):
        resp = await client.post(
            "/webhooks/acme",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    async def test_oversized_payload_returns_413(self, client, setup):
        big_payload = {"data": "x" * 2_000_000}
        resp = await client.post("/webhooks/acme", json=big_payload)
        assert resp.status_code == 413


class TestWebhookDuplication:
    async def test_duplicate_event_id_returns_200(self, client, setup):
        r1 = await client.post(
            "/webhooks/acme", json={"data": "test"}, headers={"X-Event-ID": "dedup-001"}
        )
        r2 = await client.post(
            "/webhooks/acme", json={"data": "test"}, headers={"X-Event-ID": "dedup-001"}
        )
        assert r1.status_code == 202
        assert r2.status_code == 200
        assert r2.json()["status"] == "duplicate"
