from src.schemas.events import EventType
from src.schemas.shipment import ShipmentUpdateV1
from src.schemas.invoice import InvoiceV1
from src.services.schema_registry import SchemaRegistryEntry, create_default_registry
from src.services.prompts import (
    build_classification_prompt,
    build_shipment_extraction_prompt,
    build_invoice_extraction_prompt,
)


class TestSchemaRegistry:
    def test_default_registry_has_shipment(self):
        reg = create_default_registry()
        entry = reg.get(EventType.SHIPMENT_UPDATE)
        assert entry is not None
        assert entry.schema_class is ShipmentUpdateV1

    def test_default_registry_has_invoice(self):
        reg = create_default_registry()
        entry = reg.get(EventType.INVOICE)
        assert entry is not None
        assert entry.schema_class is InvoiceV1

    def test_unclassified_returns_none(self):
        reg = create_default_registry()
        assert reg.get(EventType.UNCLASSIFIED) is None

    def test_supported_types_returns_two(self):
        reg = create_default_registry()
        types = reg.supported_types()
        assert len(types) == 2
        assert EventType.SHIPMENT_UPDATE in types
        assert EventType.INVOICE in types

    def test_register_new_type(self):
        reg = create_default_registry()
        # Can register a new type dynamically
        entry = SchemaRegistryEntry(
            schema_class=ShipmentUpdateV1,
            prompt_builder=build_shipment_extraction_prompt,
            version="99.0",
        )
        reg.register(EventType.UNCLASSIFIED, entry)
        assert reg.get(EventType.UNCLASSIFIED) is not None
        assert len(reg.supported_types()) == 3

    def test_registry_entry_has_version(self):
        reg = create_default_registry()
        entry = reg.get(EventType.SHIPMENT_UPDATE)
        assert entry is not None
        assert entry.version == "1.0"

    def test_registry_entry_prompt_builder_callable(self):
        reg = create_default_registry()
        entry = reg.get(EventType.INVOICE)
        assert entry is not None
        result = entry.prompt_builder({"amount": 100, "currency": "USD"})
        assert isinstance(result, str)
        assert len(result) > 20


class TestPromptBuilders:
    def test_classification_prompt_contains_raw_json(self):
        payload = {"tracking": "SHIP-001", "status": "in transit"}
        prompt = build_classification_prompt(payload)
        assert "SHIP-001" in prompt
        assert "SHIPMENT_UPDATE" in prompt
        assert "INVOICE" in prompt
        assert "UNCLASSIFIED" in prompt

    def test_shipment_extraction_prompt_contains_json(self):
        payload = {"vendor_id": "acme", "tracking_number": "TRK-123"}
        prompt = build_shipment_extraction_prompt(payload)
        assert "TRK-123" in prompt
        assert len(prompt) > 50

    def test_invoice_extraction_prompt_contains_json(self):
        payload = {"invoice_id": "INV-001", "amount": 250.00}
        prompt = build_invoice_extraction_prompt(payload)
        assert "INV-001" in prompt
        assert len(prompt) > 50
