from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    SHIPMENT_UPDATE = "SHIPMENT_UPDATE"
    INVOICE = "INVOICE"
    UNCLASSIFIED = "UNCLASSIFIED"


class ClassificationResult(BaseModel):
    event_type: EventType
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
