# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportMissingTypeArgument=false, reportUnusedCallResult=false

import uuid

import pytest
from sqlalchemy import select

from src.db import create_engine, create_session_factory, init_db
from src.models import NormalizedRecord, ProcessingAttempt, RawEvent
from src.pipeline.processor import ProcessingPipeline, ProcessingResult
from src.schemas.events import EventType
from src.services.llm.mock import MockLLMService
from src.services.schema_registry import create_default_registry


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://")
    await init_db(engine)
    yield create_session_factory(engine)
    await engine.dispose()


def make_pipeline(session_factory, confidence_threshold=0.7, llm_seed=42):
    return ProcessingPipeline(
        session_factory=session_factory,
        llm_service=MockLLMService(delay_seconds=0.0, seed=llm_seed),
        schema_registry=create_default_registry(),
        confidence_threshold=confidence_threshold,
    )


async def insert_raw_event(
    session_factory, raw_payload: dict, vendor="acme", status="QUEUED"
) -> str:
    raw_id = str(uuid.uuid4())
    async with session_factory() as session:
        session.add(
            RawEvent(
                id=raw_id,
                ingestion_id=f"ing_{raw_id[:8]}",
                vendor=vendor,
                weak_payload_hash=raw_id,
                content_type="application/json",
                raw_payload_json=raw_payload,
                status=status,
            )
        )
        await session.commit()
    return raw_id


class TestProcessingPipelineShipment:
    async def test_shipment_payload_processed_to_completed(self, session_factory):
        payload = {
            "vendor_id": "acme",
            "tracking_number": "SHIP-001",
            "status": "TRANSIT",
            "timestamp": "2024-01-15T10:30:00Z",
        }
        raw_id = await insert_raw_event(session_factory, payload)
        pipeline = make_pipeline(session_factory)

        result = await pipeline.process(raw_id)

        assert result.status == "COMPLETED"
        assert result.event_type == EventType.SHIPMENT_UPDATE
        assert result.normalized_record_id is not None
        assert result.quarantine_id is None

    async def test_shipment_normalized_record_in_db(self, session_factory):
        payload = {
            "vendor_id": "acme",
            "tracking_number": "SHIP-002",
            "status": "DELIVERED",
            "timestamp": "2024-02-01T08:00:00Z",
        }
        raw_id = await insert_raw_event(session_factory, payload)
        pipeline = make_pipeline(session_factory)
        result = await pipeline.process(raw_id)

        async with session_factory() as session:
            nr = await session.get(NormalizedRecord, result.normalized_record_id)
            assert nr is not None
            assert nr.record_type == "SHIPMENT_UPDATE"
            assert nr.schema_version == "1.0"


class TestProcessingPipelineInvoice:
    async def test_invoice_payload_processed_to_completed(self, session_factory):
        payload = {
            "vendor_id": "vendor-1",
            "invoice_id": "INV-001",
            "amount": 150.50,
            "currency": "USD",
        }
        raw_id = await insert_raw_event(session_factory, payload)
        pipeline = make_pipeline(session_factory)

        result = await pipeline.process(raw_id)

        assert result.status == "COMPLETED"
        assert result.event_type == EventType.INVOICE
        assert result.normalized_record_id is not None


class TestProcessingPipelineUnclassified:
    async def test_garbage_payload_classified_as_unclassified(self, session_factory):
        payload = {"random_key": "random_value", "xyz": 123}
        raw_id = await insert_raw_event(session_factory, payload)
        pipeline = make_pipeline(session_factory)

        result = await pipeline.process(raw_id)

        assert result.status == "COMPLETED"
        assert result.event_type == EventType.UNCLASSIFIED
        assert result.normalized_record_id is None


class TestProcessingPipelineQuarantine:
    async def test_low_confidence_triggers_quarantine(self, session_factory):
        payload = {
            "vendor_id": "acme",
            "tracking_number": "SHIP-003",
            "status": "TRANSIT",
            "timestamp": "2024-01-15T10:30:00Z",
        }
        raw_id = await insert_raw_event(session_factory, payload)
        pipeline = make_pipeline(session_factory, confidence_threshold=0.99)

        result = await pipeline.process(raw_id)

        assert result.quarantine_id is not None
        assert result.normalized_record_id is None


class TestProcessingPipelineAttempts:
    async def test_processing_attempts_recorded(self, session_factory):
        payload = {
            "vendor_id": "acme",
            "tracking_number": "SHIP-004",
            "status": "TRANSIT",
            "timestamp": "2024-01-15T10:30:00Z",
        }
        raw_id = await insert_raw_event(session_factory, payload)
        pipeline = make_pipeline(session_factory)
        await pipeline.process(raw_id)

        async with session_factory() as session:
            result = await session.execute(
                select(ProcessingAttempt).where(ProcessingAttempt.raw_event_id == raw_id)
            )
            attempts = result.scalars().all()
        assert len(attempts) >= 1

    async def test_result_is_processing_result_dataclass(self, session_factory):
        raw_id = await insert_raw_event(session_factory, {"foo": "bar"})
        pipeline = make_pipeline(session_factory)
        result = await pipeline.process(raw_id)
        assert isinstance(result, ProcessingResult)
        assert hasattr(result, "status")
        assert hasattr(result, "event_type")
