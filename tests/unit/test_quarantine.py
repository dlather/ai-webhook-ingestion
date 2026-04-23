# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false

import uuid

import pytest

from src.db import create_engine, create_session_factory, init_db
from src.models import RawEvent, QuarantineEvent as QuarantineEventModel
from src.services.quarantine import QuarantineService, QuarantineReasonCode


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://")
    await init_db(engine)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def raw_event_id(session_factory):
    raw_id = str(uuid.uuid4())
    async with session_factory() as session:
        session.add(
            RawEvent(
                id=raw_id,
                ingestion_id="ing_test",
                vendor="acme",
                weak_payload_hash="testhash",
                content_type="application/json",
                raw_payload_json={"test": 1},
                status="PROCESSING",
            )
        )
        await session.commit()
    return raw_id


class TestQuarantineReasonCode:
    def test_constants_exist(self):
        assert QuarantineReasonCode.LOW_CONFIDENCE == "LOW_CONFIDENCE"
        assert QuarantineReasonCode.VALIDATION_FAILURE == "VALIDATION_FAILURE"
        assert QuarantineReasonCode.EXTRACTION_FAILURE == "EXTRACTION_FAILURE"
        assert QuarantineReasonCode.LLM_ERROR == "LLM_ERROR"
        assert QuarantineReasonCode.UNKNOWN_TYPE == "UNKNOWN_TYPE"


class TestQuarantineService:
    async def test_quarantine_creates_record(self, session_factory, raw_event_id):
        async with session_factory() as session:
            svc = QuarantineService(session)
            q = await svc.quarantine(
                raw_event_id,
                QuarantineReasonCode.LOW_CONFIDENCE,
                "Confidence 0.4 below threshold 0.7",
            )
        assert isinstance(q, QuarantineEventModel)
        assert q.id is not None
        assert q.reason_code == "LOW_CONFIDENCE"
        assert q.review_status == "PENDING"

    async def test_quarantine_updates_raw_event_status(self, session_factory, raw_event_id):
        async with session_factory() as session:
            svc = QuarantineService(session)
            await svc.quarantine(
                raw_event_id,
                QuarantineReasonCode.VALIDATION_FAILURE,
                "Schema validation failed",
            )

        async with session_factory() as session:
            raw = await session.get(RawEvent, raw_event_id)
            assert raw.status == "QUARANTINED"

    async def test_quarantine_persists_llm_output(self, session_factory, raw_event_id):
        llm_output = {"raw_response": "some garbled output", "error": "parse failed"}
        async with session_factory() as session:
            svc = QuarantineService(session)
            q = await svc.quarantine(
                raw_event_id,
                QuarantineReasonCode.EXTRACTION_FAILURE,
                "Extraction failed after retries",
                raw_llm_output=llm_output,
            )
        assert q.raw_llm_output_json == llm_output

    async def test_get_quarantined_returns_record(self, session_factory, raw_event_id):
        async with session_factory() as session:
            svc = QuarantineService(session)
            created = await svc.quarantine(
                raw_event_id,
                QuarantineReasonCode.LLM_ERROR,
                "LLM timeout",
            )

        async with session_factory() as session:
            svc = QuarantineService(session)
            found = await svc.get_quarantined(raw_event_id)
        assert found is not None
        assert found.id == created.id

    async def test_get_quarantined_returns_none_when_missing(self, session_factory):
        async with session_factory() as session:
            svc = QuarantineService(session)
            result = await svc.get_quarantined("nonexistent-raw-event-id")
        assert result is None
