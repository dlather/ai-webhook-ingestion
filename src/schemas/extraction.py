from pydantic import BaseModel

from src.schemas.events import EventType
from src.schemas.invoice import InvoiceV1
from src.schemas.shipment import ShipmentUpdateV1


class ExtractionResult(BaseModel):
    event_type: EventType
    data: ShipmentUpdateV1 | InvoiceV1
    confidence: float
    model_name: str
    prompt_version: str
