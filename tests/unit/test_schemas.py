import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from src.schemas.events import EventType, ClassificationResult
from src.schemas.shipment import ShipmentStatus, ShipmentUpdateV1
from src.schemas.invoice import InvoiceV1
from src.schemas.extraction import ExtractionResult


class TestEventType:
    def test_has_three_values(self):
        assert len(EventType) == 3

    def test_values(self):
        assert EventType.SHIPMENT_UPDATE
        assert EventType.INVOICE
        assert EventType.UNCLASSIFIED


class TestClassificationResult:
    def test_valid(self):
        r = ClassificationResult(
            event_type=EventType.SHIPMENT_UPDATE,
            confidence=0.92,
            reasoning="contains tracking number",
        )
        assert r.event_type == EventType.SHIPMENT_UPDATE
        assert r.confidence == 0.92

    def test_confidence_too_high_rejected(self):
        with pytest.raises(ValidationError):
            _ = ClassificationResult(event_type=EventType.INVOICE, confidence=1.5, reasoning="test")

    def test_confidence_negative_rejected(self):
        with pytest.raises(ValidationError):
            _ = ClassificationResult(
                event_type=EventType.UNCLASSIFIED, confidence=-0.1, reasoning="test"
            )


class TestShipmentUpdateV1:
    def test_valid(self):
        s = ShipmentUpdateV1(
            vendor_id="acme",
            tracking_number="SHIP-001",
            status=ShipmentStatus.TRANSIT,
            timestamp=datetime(2024, 1, 15, 10, 30, tzinfo=timezone.utc),
        )
        assert s.tracking_number == "SHIP-001"
        assert s.status == ShipmentStatus.TRANSIT

    def test_status_from_string(self):
        s = ShipmentUpdateV1.model_validate(
            {
                "vendor_id": "acme",
                "tracking_number": "TRK-1",
                "status": "DELIVERED",
                "timestamp": "2024-01-15T10:30:00Z",
            }
        )
        assert s.status == ShipmentStatus.DELIVERED

    def test_empty_tracking_number_rejected(self):
        with pytest.raises(ValidationError):
            _ = ShipmentUpdateV1.model_validate(
                {
                    "vendor_id": "acme",
                    "tracking_number": "",
                    "status": ShipmentStatus.TRANSIT,
                    "timestamp": "2024-01-15T10:30:00Z",
                }
            )

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            _ = ShipmentUpdateV1.model_validate(
                {
                    "vendor_id": "acme",
                    "tracking_number": "SHIP-001",
                    "status": "UNKNOWN_STATUS",
                    "timestamp": "2024-01-15T10:30:00Z",
                }
            )

    def test_model_dump_json(self):
        s = ShipmentUpdateV1.model_validate(
            {
                "vendor_id": "acme",
                "tracking_number": "SHIP-001",
                "status": "TRANSIT",
                "timestamp": "2024-01-15T10:30:00Z",
            }
        )
        json_str = s.model_dump_json()
        assert "SHIP-001" in json_str


class TestInvoiceV1:
    def test_valid(self):
        inv = InvoiceV1(vendor_id="vendor-1", invoice_id="INV-001", amount=150.50, currency="USD")
        assert inv.amount == 150.50
        assert inv.currency == "USD"

    def test_negative_amount_rejected(self):
        with pytest.raises(ValidationError):
            _ = InvoiceV1(vendor_id="v1", invoice_id="INV-1", amount=-50.0, currency="USD")

    def test_zero_amount_rejected(self):
        with pytest.raises(ValidationError):
            _ = InvoiceV1(vendor_id="v1", invoice_id="INV-1", amount=0.0, currency="USD")

    def test_invalid_currency_length_rejected(self):
        with pytest.raises(ValidationError):
            _ = InvoiceV1(
                vendor_id="v1",
                invoice_id="INV-1",
                amount=100.0,
                currency="US",  # must be 3 chars
            )

    def test_lowercase_currency_rejected(self):
        with pytest.raises(ValidationError):
            _ = InvoiceV1(
                vendor_id="v1",
                invoice_id="INV-1",
                amount=100.0,
                currency="usd",  # must be uppercase
            )


class TestExtractionResult:
    def test_valid_with_shipment(self):
        shipment = ShipmentUpdateV1.model_validate(
            {
                "vendor_id": "acme",
                "tracking_number": "SHIP-001",
                "status": "TRANSIT",
                "timestamp": "2024-01-15T10:30:00Z",
            }
        )
        result = ExtractionResult(
            event_type=EventType.SHIPMENT_UPDATE,
            data=shipment,
            confidence=0.95,
            model_name="claude-sonnet-4-20250514",
            prompt_version="1.0",
        )
        assert result.event_type == EventType.SHIPMENT_UPDATE
