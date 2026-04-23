# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportMissingParameterType=false, reportAny=false

"""End-to-end integration tests using a real FastAPI app and in-memory SQLite."""

import asyncio
import uuid

import httpx
from httpx import ASGITransport
from sqlalchemy import select

from src.models import NormalizedRecord, QuarantineEvent, RawEvent


def unique_value(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class TestShipmentWebhookE2E:
    async def test_shipment_webhook_processed_to_normalized_record(
        self, client, wait_for_status_fn
    ):
        async_client, session_factory, event_queue = client

        response = await async_client.post(
            "/webhooks/acme",
            json={
                "vendor_id": unique_value("acme-shipment-vendor"),
                "tracking_number": unique_value("E2E-SHIP"),
                "status": "TRANSIT",
                "timestamp": "2024-06-01T12:00:00Z",
            },
        )

        assert response.status_code == 202
        ingestion_id = response.json()["ingestion_id"]
        await asyncio.wait_for(event_queue.join(), timeout=3.0)

        final_status = await wait_for_status_fn(
            session_factory,
            ingestion_id,
            ["COMPLETED", "QUARANTINED", "FAILED_TERMINAL"],
        )
        assert final_status == "COMPLETED"

        async with session_factory() as session:
            raw_event = (
                await session.execute(select(RawEvent).where(RawEvent.ingestion_id == ingestion_id))
            ).scalar_one()
            normalized_records = (
                (
                    await session.execute(
                        select(NormalizedRecord).where(
                            NormalizedRecord.raw_event_id == raw_event.id
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert normalized_records
        assert normalized_records[0].record_type == "SHIPMENT_UPDATE"
        assert normalized_records[0].schema_version == "1.0"


class TestInvoiceWebhookE2E:
    async def test_invoice_webhook_processed_to_normalized_record(self, client, wait_for_status_fn):
        async_client, session_factory, event_queue = client

        response = await async_client.post(
            "/webhooks/acme",
            json={
                "vendor_id": unique_value("vendor"),
                "invoice_id": unique_value("E2E-INV"),
                "amount": 250.00,
                "currency": "USD",
            },
        )

        assert response.status_code == 202
        ingestion_id = response.json()["ingestion_id"]
        await asyncio.wait_for(event_queue.join(), timeout=3.0)

        final_status = await wait_for_status_fn(
            session_factory,
            ingestion_id,
            ["COMPLETED", "QUARANTINED", "FAILED_TERMINAL"],
        )
        assert final_status == "COMPLETED"

        async with session_factory() as session:
            raw_event = (
                await session.execute(select(RawEvent).where(RawEvent.ingestion_id == ingestion_id))
            ).scalar_one()
            normalized_records = (
                (
                    await session.execute(
                        select(NormalizedRecord).where(
                            NormalizedRecord.raw_event_id == raw_event.id
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert normalized_records
        assert normalized_records[0].record_type == "INVOICE"


class TestUnclassifiedWebhookE2E:
    async def test_garbage_payload_completes_as_unclassified(self, client, wait_for_status_fn):
        async_client, session_factory, event_queue = client

        response = await async_client.post(
            "/webhooks/acme",
            json={
                "totally": unique_value("random"),
                "garbage": 42,
                "misc": True,
            },
        )

        assert response.status_code == 202
        ingestion_id = response.json()["ingestion_id"]
        await asyncio.wait_for(event_queue.join(), timeout=3.0)

        final_status = await wait_for_status_fn(
            session_factory,
            ingestion_id,
            ["COMPLETED", "QUARANTINED", "FAILED_TERMINAL"],
        )
        assert final_status == "COMPLETED"

        async with session_factory() as session:
            raw_event = (
                await session.execute(select(RawEvent).where(RawEvent.ingestion_id == ingestion_id))
            ).scalar_one()
            normalized_records = (
                (
                    await session.execute(
                        select(NormalizedRecord).where(
                            NormalizedRecord.raw_event_id == raw_event.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            quarantine_events = (
                (
                    await session.execute(
                        select(QuarantineEvent).where(QuarantineEvent.raw_event_id == raw_event.id)
                    )
                )
                .scalars()
                .all()
            )

        assert raw_event.status == "COMPLETED"
        assert normalized_records == []
        assert quarantine_events == []


class TestQuarantineWebhookE2E:
    async def test_low_confidence_payload_is_quarantined(self, app_factory, wait_for_status_fn):
        async with app_factory(0.99) as (app, session_factory, event_queue):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as async_client:
                response = await async_client.post(
                    "/webhooks/acme",
                    json={
                        "vendor_id": unique_value("acme-quarantine-vendor"),
                        "tracking_number": unique_value("E2E-LOWCONF"),
                        "status": "TRANSIT",
                        "timestamp": "2024-06-01T12:00:00Z",
                    },
                )

                assert response.status_code == 202
                ingestion_id = response.json()["ingestion_id"]
                await asyncio.wait_for(event_queue.join(), timeout=3.0)

                final_status = await wait_for_status_fn(
                    session_factory,
                    ingestion_id,
                    ["COMPLETED", "QUARANTINED", "FAILED_TERMINAL"],
                )
                assert final_status == "QUARANTINED"

                async with session_factory() as session:
                    raw_event = (
                        await session.execute(
                            select(RawEvent).where(RawEvent.ingestion_id == ingestion_id)
                        )
                    ).scalar_one()
                    normalized_records = (
                        (
                            await session.execute(
                                select(NormalizedRecord).where(
                                    NormalizedRecord.raw_event_id == raw_event.id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    quarantine_events = (
                        (
                            await session.execute(
                                select(QuarantineEvent).where(
                                    QuarantineEvent.raw_event_id == raw_event.id
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )

                assert normalized_records == []
                assert quarantine_events
                assert quarantine_events[0].reason_code == "LOW_CONFIDENCE"


class TestDeduplicationE2E:
    async def test_duplicate_event_id_detected(self, client):
        async_client, session_factory, _ = client
        event_id = unique_value("e2e-dedup")
        payload_value = unique_value("first")

        first_response = await async_client.post(
            "/webhooks/acme",
            json={"data": payload_value},
            headers={"X-Event-ID": event_id},
        )
        second_response = await async_client.post(
            "/webhooks/acme",
            json={"data": payload_value},
            headers={"X-Event-ID": event_id},
        )

        assert first_response.status_code == 202
        assert second_response.status_code == 200
        assert second_response.json()["status"] == "duplicate"

        async with session_factory() as session:
            raw_events = (
                (
                    await session.execute(
                        select(RawEvent).where(RawEvent.vendor_event_id == event_id)
                    )
                )
                .scalars()
                .all()
            )

        assert len(raw_events) == 1

    async def test_weak_dedup_same_payload_detected(self, client):
        async_client, _, _ = client
        payload = {"weak_dedup_key": unique_value("unique_content_for_weak_test_e2e")}

        first_response = await async_client.post("/webhooks/vendor-b", json=payload)
        second_response = await async_client.post("/webhooks/vendor-b", json=payload)

        assert first_response.status_code == 202
        assert second_response.status_code == 200
        assert second_response.json()["status"] == "duplicate"


class TestValidationE2E:
    async def test_wrong_content_type_returns_415(self, client):
        async_client, _, _ = client

        response = await async_client.post(
            "/webhooks/acme",
            content=b"plain text",
            headers={"Content-Type": "text/plain"},
        )

        assert response.status_code == 415

    async def test_malformed_json_returns_400(self, client):
        async_client, _, _ = client

        response = await async_client.post(
            "/webhooks/acme",
            content=b"{invalid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400


class TestHealthE2E:
    async def test_health_endpoint_returns_healthy(self, client):
        async_client, _, _ = client

        response = await async_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["db"] == "connected"
        assert "queue_depth" in data

    async def test_ingestion_lookup_after_submit(self, client, wait_for_status_fn):
        async_client, session_factory, event_queue = client

        response = await async_client.post(
            "/webhooks/acme",
            json={"lookup": unique_value("lookup")},
        )
        assert response.status_code == 202
        ingestion_id = response.json()["ingestion_id"]
        await asyncio.wait_for(event_queue.join(), timeout=3.0)

        lookup_response = await async_client.get(f"/ingestions/{ingestion_id}")
        assert lookup_response.status_code == 200
        lookup_data = lookup_response.json()
        assert lookup_data["ingestion_id"] == ingestion_id
        assert lookup_data["vendor"] == "acme"

        final_status = await wait_for_status_fn(
            session_factory,
            ingestion_id,
            ["COMPLETED", "QUARANTINED", "FAILED_TERMINAL"],
        )
        assert final_status == "COMPLETED"
