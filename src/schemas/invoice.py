from pydantic import BaseModel, Field, field_validator


class InvoiceV1(BaseModel):
    vendor_id: str
    invoice_id: str
    amount: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase(cls, v: str) -> str:
        if v != v.upper():
            raise ValueError("currency must be uppercase ISO code")
        return v
