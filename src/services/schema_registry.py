from dataclasses import dataclass
from collections.abc import Mapping
from typing import Callable

from src.schemas.events import EventType
from src.schemas.invoice import InvoiceV1
from src.schemas.shipment import ShipmentUpdateV1
from src.services.prompts import (
    build_invoice_extraction_prompt,
    build_shipment_extraction_prompt,
)


@dataclass
class SchemaRegistryEntry:
    schema_class: type
    prompt_builder: Callable[[Mapping[str, object]], str]
    version: str


class SchemaRegistry:
    def __init__(self) -> None:
        self._registry: dict[EventType, SchemaRegistryEntry] = {}

    def register(self, event_type: EventType, entry: SchemaRegistryEntry) -> None:
        self._registry[event_type] = entry

    def get(self, event_type: EventType) -> SchemaRegistryEntry | None:
        return self._registry.get(event_type)

    def supported_types(self) -> list[EventType]:
        return list(self._registry.keys())


def create_default_registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register(
        EventType.SHIPMENT_UPDATE,
        SchemaRegistryEntry(
            schema_class=ShipmentUpdateV1,
            prompt_builder=build_shipment_extraction_prompt,
            version="1.0",
        ),
    )
    registry.register(
        EventType.INVOICE,
        SchemaRegistryEntry(
            schema_class=InvoiceV1,
            prompt_builder=build_invoice_extraction_prompt,
            version="1.0",
        ),
    )
    return registry
