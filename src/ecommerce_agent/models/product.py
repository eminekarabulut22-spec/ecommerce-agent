from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FieldSource(str, Enum):
    VISION_EXTRACTION = "vision_extraction"
    WEB_SEARCH = "web_search"
    MANUAL = "manual"


class ValidationStatus(str, Enum):
    VALID = "valid"
    NEEDS_REVIEW = "needs_review"


class FieldConfidence(BaseModel):
    """A single extracted value plus how sure the agent is about it and where it came from."""

    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source: FieldSource


class ProductDraft(BaseModel):
    """Working state produced/updated by the agent while it gathers product information.

    Each attribute is optional and wrapped in `FieldConfidence` so the orchestrator can
    inspect per-field confidence and provenance to decide whether more tool calls are needed.
    """

    id: UUID = Field(default_factory=uuid4)
    source_image_url: str

    title: FieldConfidence | None = None
    description: FieldConfidence | None = None
    category: FieldConfidence | None = None
    brand: FieldConfidence | None = None
    manufacturer: FieldConfidence | None = None
    color: FieldConfidence | None = None
    material: FieldConfidence | None = None
    dimensions: FieldConfidence | None = None
    weight: FieldConfidence | None = None
    price: FieldConfidence | None = None
    currency: FieldConfidence | None = None
    gtin: FieldConfidence | None = None

    tags: list[str] = Field(default_factory=list)
    raw_llm_responses: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)

    _CORE_FIELDS: tuple[str, ...] = (
        "title",
        "description",
        "category",
        "price",
    )

    def missing_fields(self, required: Iterable[str] | None = None) -> list[str]:
        """Names of required fields that have not been populated at all."""
        required = tuple(required) if required is not None else self._CORE_FIELDS
        return [name for name in required if getattr(self, name) is None]

    def low_confidence_fields(self, threshold: float) -> list[str]:
        """Names of populated fields whose confidence is below the given threshold."""
        return [
            name
            for name in type(self).model_fields
            if isinstance(getattr(self, name, None), FieldConfidence)
            and getattr(self, name).confidence < threshold
        ]


class Product(BaseModel):
    """Final, flattened, persistable product record."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    description: str | None = None
    category: str | None = None
    brand: str | None = None
    manufacturer: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    price: float | None = Field(default=None, ge=0)
    currency: str | None = None
    gtin: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_image_url: str

    validation_status: ValidationStatus = ValidationStatus.NEEDS_REVIEW
    validation_errors: list[str] = Field(default_factory=list)
    confidence_scores: dict[str, float] = Field(default_factory=dict)
    extraction_model: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @classmethod
    def from_draft(cls, draft: ProductDraft, *, extraction_model: str | None = None) -> Product:
        """Flatten a `ProductDraft` into a `Product`, dropping per-field provenance
        into `confidence_scores` and collecting free-form attributes."""

        def value_of(field: FieldConfidence | None) -> Any:
            return field.value if field is not None else None

        confidence_scores = {
            name: getattr(draft, name).confidence
            for name in type(draft).model_fields
            if isinstance(getattr(draft, name, None), FieldConfidence)
        }

        attributes = {
            name: str(value_of(getattr(draft, name)))
            for name in ("color", "material", "dimensions", "weight")
            if getattr(draft, name) is not None
        }

        return cls(
            id=draft.id,
            title=value_of(draft.title) or "Untitled product",
            description=value_of(draft.description),
            category=value_of(draft.category),
            brand=value_of(draft.brand),
            manufacturer=value_of(draft.manufacturer),
            attributes=attributes,
            price=value_of(draft.price),
            currency=value_of(draft.currency),
            gtin=value_of(draft.gtin),
            tags=draft.tags,
            source_image_url=draft.source_image_url,
            confidence_scores=confidence_scores,
            extraction_model=extraction_model,
        )
