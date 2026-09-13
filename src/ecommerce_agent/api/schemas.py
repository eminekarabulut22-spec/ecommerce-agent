from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from ecommerce_agent.agent.orchestrator import AgentOutcome, AgentRunResult
from ecommerce_agent.agent.trace import AgentRunRecord
from ecommerce_agent.models.product import Product, ValidationStatus


class HealthResponse(BaseModel):
    status: str = "ok"


class ErrorResponse(BaseModel):
    error: str
    detail: str


class HumanReviewInfo(BaseModel):
    """Surfaced only when the run needs a human to look at it."""

    reason: str
    validation_errors: list[str]
    validation_warnings: list[str]


class ProcessProductResponse(BaseModel):
    run_id: UUID
    outcome: AgentOutcome
    reason: str
    iterations_used: int
    product: Product | None = None
    duplicate_of_product_id: str | None = None
    human_review: HumanReviewInfo | None = None
    trace: AgentRunRecord

    @classmethod
    def from_agent_result(cls, result: AgentRunResult) -> "ProcessProductResponse":
        human_review: HumanReviewInfo | None = None
        if result.outcome in (AgentOutcome.NEEDS_REVIEW, AgentOutcome.DUPLICATE):
            human_review = HumanReviewInfo(
                reason=result.reason,
                validation_errors=result.validation.errors if result.validation else [],
                validation_warnings=result.validation.warnings if result.validation else [],
            )

        return cls(
            run_id=result.trace.run_id,
            outcome=result.outcome,
            reason=result.reason,
            iterations_used=result.iterations_used,
            product=result.product,
            duplicate_of_product_id=result.duplicate_of_product_id,
            human_review=human_review,
            trace=result.trace,
        )


def _resolve_image_url(source_image_url: str) -> str:
    """Turn a stored `source_image_url` into something a browser can actually fetch.

    The agent never persists uploaded image bytes anywhere - `source_image_url` is just
    whatever string was supplied at process time (an uploaded filename, or a seeded demo
    product's relative path). If it's already an absolute URL, use it as-is; otherwise treat
    it as a filename and resolve it against the `/sample-images` static mount.
    """
    if source_image_url.startswith(("http://", "https://")):
        return source_image_url
    filename = source_image_url.rsplit("/", 1)[-1]
    return f"/sample-images/{filename}"


def _is_demo_product(product: Product) -> bool:
    """Demo/seed products are never run through the LLM pipeline, so they never get an
    `extraction_model`; the seed script also tags them explicitly as a second signal."""
    return product.extraction_model is None or "demo" in product.tags


class ProductListItem(BaseModel):
    id: UUID
    title: str
    description: str | None = None
    category: str | None = None
    brand: str | None = None
    manufacturer: str | None = None
    price: float | None = None
    currency: str | None = None
    validation_status: ValidationStatus
    validation_errors: list[str] = []
    confidence_scores: dict[str, float] = {}
    tags: list[str] = []
    image_url: str
    is_demo: bool
    extraction_model: str | None = None
    created_at: datetime

    @classmethod
    def from_product(cls, product: Product) -> "ProductListItem":
        return cls(
            id=product.id,
            title=product.title,
            description=product.description,
            category=product.category,
            brand=product.brand,
            manufacturer=product.manufacturer,
            price=product.price,
            currency=product.currency,
            validation_status=product.validation_status,
            validation_errors=product.validation_errors,
            confidence_scores=product.confidence_scores,
            tags=product.tags,
            image_url=_resolve_image_url(product.source_image_url),
            is_demo=_is_demo_product(product),
            extraction_model=product.extraction_model,
            created_at=product.created_at,
        )


class ProductListResponse(BaseModel):
    products: list[ProductListItem]
    count: int
