import asyncio
import random
from typing import Any

from pydantic import BaseModel

from src.schemas.events import ClassificationResult, EventType
from src.schemas.invoice import InvoiceV1
from src.schemas.shipment import ShipmentStatus, ShipmentUpdateV1

_SHIPMENT_KEYS = {"tracking", "tracking_number", "shipment", "shipment_id", "transit"}
_INVOICE_KEYS = {"invoice", "invoice_id", "amount", "total", "billing", "payment"}


class MockLLMService:
    def __init__(
        self,
        delay_seconds: float = 0.1,
        failure_rate: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self._delay = delay_seconds
        self._failure_rate = failure_rate
        self._rng = random.Random(seed)

    async def classify(self, raw_payload: dict) -> ClassificationResult:
        await asyncio.sleep(self._delay)

        if self._failure_rate > 0 and self._rng.random() < self._failure_rate:
            raise TimeoutError("Mock LLM simulated timeout")

        payload_keys = {k.lower() for k in raw_payload.keys()}
        payload_values = " ".join(str(v).lower() for v in raw_payload.values())

        if payload_keys & _SHIPMENT_KEYS or any(
            word in payload_values for word in ("transit", "tracking", "shipment", "deliver")
        ):
            return ClassificationResult(
                event_type=EventType.SHIPMENT_UPDATE,
                confidence=0.92,
                reasoning="Payload contains shipment/tracking keywords",
            )

        if payload_keys & _INVOICE_KEYS or any(
            word in payload_values for word in ("invoice", "amount", "billing", "payment")
        ):
            return ClassificationResult(
                event_type=EventType.INVOICE,
                confidence=0.89,
                reasoning="Payload contains invoice/billing keywords",
            )

        return ClassificationResult(
            event_type=EventType.UNCLASSIFIED,
            confidence=0.95,
            reasoning="No recognizable event type keywords found",
        )

    async def extract(
        self,
        raw_payload: dict,
        event_type: EventType,
        schema_class: type[BaseModel],
    ) -> BaseModel:
        await asyncio.sleep(self._delay)

        if event_type == EventType.SHIPMENT_UPDATE:
            return self._extract_shipment(raw_payload)
        elif event_type == EventType.INVOICE:
            return self._extract_invoice(raw_payload)
        else:
            raise ValueError(f"Cannot extract for event type: {event_type}")

    def _extract_shipment(self, payload: dict) -> ShipmentUpdateV1:
        def get(keys: list[str], default: Any = None) -> Any:
            for k in keys:
                if k in payload:
                    return payload[k]
            return default

        raw_status = str(get(["status", "shipment_status", "delivery_status"], "TRANSIT")).upper()
        status_map = {
            "IN_TRANSIT": ShipmentStatus.TRANSIT,
            "IN TRANSIT": ShipmentStatus.TRANSIT,
            "SHIPPED": ShipmentStatus.TRANSIT,
            "DELIVERED": ShipmentStatus.DELIVERED,
            "COMPLETE": ShipmentStatus.DELIVERED,
            "COMPLETED": ShipmentStatus.DELIVERED,
            "EXCEPTION": ShipmentStatus.EXCEPTION,
            "FAILED": ShipmentStatus.EXCEPTION,
            "ERROR": ShipmentStatus.EXCEPTION,
            "DELAYED": ShipmentStatus.EXCEPTION,
        }
        status = status_map.get(raw_status, ShipmentStatus.TRANSIT)
        if raw_status in {"TRANSIT", "DELIVERED", "EXCEPTION"}:
            status = ShipmentStatus(raw_status)

        raw_timestamp = get(
            ["timestamp", "date", "event_time", "occurred_at"], "2024-01-01T00:00:00Z"
        )

        return ShipmentUpdateV1(
            vendor_id=str(get(["vendor_id", "vendor", "sender_id"], "unknown")),
            tracking_number=str(
                get(["tracking_number", "tracking", "shipment_id", "track_id"], "UNKNOWN")
            ),
            status=status,
            timestamp=raw_timestamp,
        )

    def _extract_invoice(self, payload: dict) -> InvoiceV1:
        def get(keys: list[str], default: Any = None) -> Any:
            for k in keys:
                if k in payload:
                    return payload[k]
            return default

        raw_currency = str(get(["currency", "currency_code"], "USD")).upper()
        if len(raw_currency) != 3:
            raw_currency = "USD"

        raw_amount = get(["amount", "total", "total_amount", "invoice_amount"], 0.01)
        try:
            amount = float(raw_amount)
            if amount <= 0:
                amount = 0.01
        except (ValueError, TypeError):
            amount = 0.01

        return InvoiceV1(
            vendor_id=str(get(["vendor_id", "vendor", "seller_id"], "unknown")),
            invoice_id=str(get(["invoice_id", "invoice_number", "bill_id"], "UNKNOWN")),
            amount=amount,
            currency=raw_currency,
        )
