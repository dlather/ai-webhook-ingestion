from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, utcnow


class NormalizedRecord(Base):
    __tablename__: str = "normalized_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    raw_event_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    def __init__(self, **kwargs: object):
        _ = kwargs.setdefault("review_flag", False)
        super().__init__(**kwargs)
