import uuid

from src.models import (
    NormalizedRecord,
    OutboxEvent,
    ProcessingAttempt,
    QuarantineEvent,
    RawEvent,
)


def make_uuid():
    return str(uuid.uuid4())


class TestRawEvent:
    def test_table_name(self):
        assert RawEvent.__tablename__ == "raw_events"

    def test_instantiation(self):
        r = RawEvent(
            id=make_uuid(),
            ingestion_id="ing_abc123",
            vendor="acme",
            weak_payload_hash="sha256hash",
            content_type="application/json",
            raw_payload_json={"test": 1},
            status="RECEIVED",
        )
        assert r.vendor == "acme"
        assert r.status == "RECEIVED"

    def test_optional_fields_default_none(self):
        r = RawEvent(
            id=make_uuid(),
            ingestion_id="ing_xyz",
            vendor="test",
            weak_payload_hash="hash",
            content_type="application/json",
            raw_payload_json={},
            status="RECEIVED",
        )
        assert r.vendor_event_id is None
        assert r.strong_dedupe_key is None


class TestOutboxEvent:
    def test_table_name(self):
        assert OutboxEvent.__tablename__ == "outbox_events"

    def test_instantiation(self):
        o = OutboxEvent(
            id=make_uuid(),
            aggregate_type="raw_event",
            aggregate_id=make_uuid(),
            event_type="webhook.received",
            payload_json={},
            status="PENDING",
        )
        assert o.status == "PENDING"
        assert o.attempt_count == 0  # default

    def test_processing_started_at_nullable(self):
        o = OutboxEvent(
            id=make_uuid(),
            aggregate_type="raw_event",
            aggregate_id=make_uuid(),
            event_type="webhook.received",
            payload_json={},
            status="PENDING",
        )
        assert o.processing_started_at is None


class TestProcessingAttempt:
    def test_table_name(self):
        assert ProcessingAttempt.__tablename__ == "processing_attempts"

    def test_instantiation(self):
        p = ProcessingAttempt(
            id=make_uuid(),
            raw_event_id=make_uuid(),
            stage="CLASSIFY",
            attempt_no=1,
            status="SUCCESS",
        )
        assert p.stage == "CLASSIFY"


class TestNormalizedRecord:
    def test_table_name(self):
        assert NormalizedRecord.__tablename__ == "normalized_records"

    def test_instantiation(self):
        n = NormalizedRecord(
            id=make_uuid(),
            raw_event_id=make_uuid(),
            record_type="SHIPMENT_UPDATE",
            schema_version="1.0",
            normalized_payload_json={"vendor_id": "acme"},
        )
        assert n.record_type == "SHIPMENT_UPDATE"

    def test_review_flag_defaults_false(self):
        n = NormalizedRecord(
            id=make_uuid(),
            raw_event_id=make_uuid(),
            record_type="INVOICE",
            schema_version="1.0",
            normalized_payload_json={},
        )
        assert n.review_flag is False


class TestQuarantineEvent:
    def test_table_name(self):
        assert QuarantineEvent.__tablename__ == "quarantine_events"

    def test_instantiation(self):
        q = QuarantineEvent(
            id=make_uuid(),
            raw_event_id=make_uuid(),
            reason_code="LOW_CONFIDENCE",
            reason_details="Confidence 0.4 below threshold 0.7",
            review_status="PENDING",
        )
        assert q.reason_code == "LOW_CONFIDENCE"
        assert q.review_status == "PENDING"

    def test_all_five_tables_exist(self):
        tables = {
            RawEvent.__tablename__,
            OutboxEvent.__tablename__,
            ProcessingAttempt.__tablename__,
            NormalizedRecord.__tablename__,
            QuarantineEvent.__tablename__,
        }
        assert tables == {
            "raw_events",
            "outbox_events",
            "processing_attempts",
            "normalized_records",
            "quarantine_events",
        }
