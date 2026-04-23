from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, utcnow


class QuarantineEvent(Base):
    __tablename__: str = "quarantine_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    raw_event_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_details: Mapped[str] = mapped_column(Text, nullable=False)
    raw_llm_output_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    def __init__(self, **kwargs: object):
        _ = kwargs.setdefault("review_status", "PENDING")
        super().__init__(**kwargs)
