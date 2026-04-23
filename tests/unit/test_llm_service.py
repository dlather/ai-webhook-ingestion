import asyncio
import pytest

from src.schemas.events import EventType, ClassificationResult
from src.schemas.shipment import ShipmentUpdateV1, ShipmentStatus
from src.schemas.invoice import InvoiceV1
from src.services.llm.mock import MockLLMService
from src.services.llm.protocol import LLMService


class TestLLMServiceProtocol:
    def test_mock_satisfies_protocol(self):
        svc = MockLLMService()
        assert hasattr(svc, "classify")
        assert hasattr(svc, "extract")
        assert callable(svc.classify)
        assert callable(svc.extract)


class TestMockLLMServiceClassify:
    async def test_classify_shipment_payload(self):
        svc = MockLLMService(delay_seconds=0.0, seed=42)
        result = await svc.classify(
            {"tracking_number": "SHIP-001", "status": "in transit", "vendor": "acme"}
        )
        assert result.event_type == EventType.SHIPMENT_UPDATE
        assert result.confidence > 0.7
        assert isinstance(result.reasoning, str)

    async def test_classify_invoice_payload(self):
        svc = MockLLMService(delay_seconds=0.0, seed=42)
        result = await svc.classify({"invoice_id": "INV-001", "amount": 250.00, "currency": "USD"})
        assert result.event_type == EventType.INVOICE
        assert result.confidence > 0.7

    async def test_classify_garbage_as_unclassified(self):
        svc = MockLLMService(delay_seconds=0.0, seed=42)
        result = await svc.classify({"foo": 123, "bar": "baz", "xyz": True})
        assert result.event_type == EventType.UNCLASSIFIED

    async def test_classify_returns_classification_result(self):
        svc = MockLLMService(delay_seconds=0.0, seed=42)
        result = await svc.classify({"test": "data"})
        assert isinstance(result, ClassificationResult)

    async def test_classify_simulates_delay(self):
        svc = MockLLMService(delay_seconds=0.05, seed=42)
        import time

        start = time.monotonic()
        await svc.classify({"data": "test"})
        elapsed = time.monotonic() - start
        assert elapsed >= 0.04  # allow some timing tolerance


class TestMockLLMServiceExtract:
    async def test_extract_shipment_from_payload(self):
        svc = MockLLMService(delay_seconds=0.0, seed=42)
        payload = {
            "vendor_id": "acme",
            "tracking_number": "SHIP-001",
            "status": "TRANSIT",
            "timestamp": "2024-01-15T10:30:00Z",
        }
        result = await svc.extract(payload, EventType.SHIPMENT_UPDATE, ShipmentUpdateV1)
        assert isinstance(result, ShipmentUpdateV1)
        assert result.tracking_number == "SHIP-001"
        assert result.vendor_id == "acme"
        assert result.status == ShipmentStatus.TRANSIT

    async def test_extract_invoice_from_payload(self):
        svc = MockLLMService(delay_seconds=0.0, seed=42)
        payload = {
            "vendor_id": "vendor-1",
            "invoice_id": "INV-001",
            "amount": 150.50,
            "currency": "USD",
        }
        result = await svc.extract(payload, EventType.INVOICE, InvoiceV1)
        assert isinstance(result, InvoiceV1)
        assert result.invoice_id == "INV-001"
        assert result.amount == 150.50
        assert result.currency == "USD"

    async def test_extract_returns_pydantic_model(self):
        svc = MockLLMService(delay_seconds=0.0, seed=42)
        payload = {
            "vendor_id": "acme",
            "tracking_number": "TRK-100",
            "status": "DELIVERED",
            "timestamp": "2024-06-01T12:00:00Z",
        }
        result = await svc.extract(payload, EventType.SHIPMENT_UPDATE, ShipmentUpdateV1)
        assert hasattr(result, "model_dump")
