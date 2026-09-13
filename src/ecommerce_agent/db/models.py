import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ecommerce_agent.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductORM(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    brand: Mapped[str | None] = mapped_column(String, nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    gtin: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source_image_url: Mapped[str] = mapped_column(String, nullable=False)

    validation_status: Mapped[str] = mapped_column(String, nullable=False)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    confidence_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    extraction_model: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
