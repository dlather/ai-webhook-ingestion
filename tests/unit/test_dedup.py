# pyright: reportAttributeAccessIssue=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedVariable=false
import uuid

import pytest

from src.db import create_engine, create_session_factory, init_db
from src.models import RawEvent
from src.services import (
    DeduplicationService,
    canonicalize_json,
    compute_payload_hash,
    derive_strong_dedupe_key,
    derive_weak_dedupe_key,
)


class TestCanonicalizeJson:
    def test_sorts_keys(self):
        result = canonicalize_json({"b": 2, "a": 1})
        assert result == canonicalize_json({"a": 1, "b": 2})

    def test_nested_keys_sorted(self):
        result1 = canonicalize_json({"z": {"b": 1, "a": 2}})
        result2 = canonicalize_json({"z": {"a": 2, "b": 1}})
        assert result1 == result2

    def test_produces_string(self):
        result = canonicalize_json({"key": "value"})
        assert isinstance(result, str)


class TestComputePayloadHash:
    def test_same_payload_same_hash(self):
        p1 = canonicalize_json({"b": 2, "a": 1})
        p2 = canonicalize_json({"a": 1, "b": 2})
        assert compute_payload_hash(p1) == compute_payload_hash(p2)

    def test_different_payload_different_hash(self):
        p1 = canonicalize_json({"a": 1})
        p2 = canonicalize_json({"a": 2})
        assert compute_payload_hash(p1) != compute_payload_hash(p2)

    def test_returns_hex_string(self):
        h = compute_payload_hash(canonicalize_json({"test": True}))
        assert isinstance(h, str)
        assert len(h) == 64


class TestDeriveKeys:
    def test_strong_key_with_event_id(self):
        key = derive_strong_dedupe_key("acme", "evt-123")
        assert key == "acme:evt-123"

    def test_strong_key_none_without_event_id(self):
        key = derive_strong_dedupe_key("acme", None)
        assert key is None

    def test_weak_key_format(self):
        key = derive_weak_dedupe_key("acme", {"data": "test"})
        assert key.startswith("acme:")
        assert len(key) > 10


class TestDeduplicationService:
    @pytest.fixture
    async def session_factory(self):
        engine = create_engine("sqlite+aiosqlite://")
        await init_db(engine)
        yield create_session_factory(engine)
        await engine.dispose()

    async def test_no_duplicate_on_empty_db(self, session_factory):
        async with session_factory() as session:
            svc = DeduplicationService(session)
            is_dup, existing_id = await svc.check_duplicate("acme", "evt-001", {"payload": "data"})

        assert is_dup is False
        assert existing_id is None

    async def test_strong_dedup_detects_duplicate(self, session_factory):
        raw_id = str(uuid.uuid4())

        async with session_factory() as session:
            session.add(
                RawEvent(
                    id=raw_id,
                    ingestion_id="ing_first",
                    vendor="acme",
                    vendor_event_id="evt-123",
                    strong_dedupe_key="acme:evt-123",
                    weak_payload_hash="somehash",
                    content_type="application/json",
                    raw_payload_json={},
                    status="RECEIVED",
                )
            )
            await session.commit()

        async with session_factory() as session:
            svc = DeduplicationService(session)
            is_dup, existing_id = await svc.check_duplicate("acme", "evt-123", {"any": "payload"})

        assert is_dup is True
        assert existing_id == "ing_first"

    async def test_weak_dedup_detects_same_payload(self, session_factory):
        payload = {"tracking": "SHIP-001", "status": "transit"}
        canonical = canonicalize_json(payload)
        weak_hash = compute_payload_hash(canonical)
        raw_id = str(uuid.uuid4())

        async with session_factory() as session:
            session.add(
                RawEvent(
                    id=raw_id,
                    ingestion_id="ing_second",
                    vendor="acme",
                    weak_payload_hash=weak_hash,
                    content_type="application/json",
                    raw_payload_json=payload,
                    status="RECEIVED",
                )
            )
            await session.commit()

        async with session_factory() as session:
            svc = DeduplicationService(session)
            is_dup, existing_id = await svc.check_duplicate("acme", None, payload)

        assert is_dup is True
        assert existing_id == "ing_second"

    async def test_different_vendor_not_duplicate(self, session_factory):
        payload = {"data": "same"}
        canonical = canonicalize_json(payload)
        weak_hash = compute_payload_hash(canonical)
        raw_id = str(uuid.uuid4())

        async with session_factory() as session:
            session.add(
                RawEvent(
                    id=raw_id,
                    ingestion_id="ing_third",
                    vendor="vendor-a",
                    weak_payload_hash=weak_hash,
                    content_type="application/json",
                    raw_payload_json=payload,
                    status="RECEIVED",
                )
            )
            await session.commit()

        async with session_factory() as session:
            svc = DeduplicationService(session)
            is_dup, existing_id = await svc.check_duplicate("vendor-b", None, payload)

        assert is_dup is False
