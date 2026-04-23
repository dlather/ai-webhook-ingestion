from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class ShipmentStatus(str, Enum):
    TRANSIT = "TRANSIT"
    DELIVERED = "DELIVERED"
    EXCEPTION = "EXCEPTION"


class ShipmentUpdateV1(BaseModel):
    vendor_id: str
    tracking_number: str = Field(..., min_length=1)
    status: ShipmentStatus
    timestamp: datetime

    @field_validator("tracking_number")
    @classmethod
    def tracking_number_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("tracking_number must not be empty")
        return v
