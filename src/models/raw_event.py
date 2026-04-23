from datetime import datetime

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, utcnow


class RawEvent(TimestampMixin, Base):
    __tablename__: str = "raw_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ingestion_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    vendor: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    vendor_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    strong_dedupe_key: Mapped[str | None] = mapped_column(String(512), nullable=True, unique=True)
    weak_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    headers_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    raw_payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RECEIVED")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
