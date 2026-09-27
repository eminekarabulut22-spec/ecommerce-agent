from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ecommerce_agent.agent.orchestrator import AgentOutcome, AgentRunResult
from ecommerce_agent.agent.trace import AgentRunRecord
from ecommerce_agent.models.order import Order, OrderItem, OrderStatus
from ecommerce_agent.models.product import Product, ValidationStatus
from ecommerce_agent.models.user import User


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


# --- Auth --------------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    # bcrypt silently ignores/truncates past 72 bytes - cap here so an over-long password is a
    # clean 422 instead of a 500 from the hashing call.
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid email address.")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class UserResponse(BaseModel):
    id: UUID
    email: str
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(id=user.id, email=user.email, created_at=user.created_at)


# --- Payments --------------------------------------------------------------------------------


class CartItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    quantity: int = Field(default=1, ge=1, le=10)


class CheckoutSessionRequest(BaseModel):
    """What the browser may send when starting checkout. `extra="forbid"` means a client that
    tries to send its own price/amount/currency gets a 422 - the amount always comes from each
    product row in the database."""

    model_config = ConfigDict(extra="forbid")

    items: list[CartItemRequest] = Field(min_length=1, max_length=20)


class CheckoutSessionResponse(BaseModel):
    order_id: UUID
    checkout_url: str


class OrderItemResponse(BaseModel):
    product_id: UUID
    title: str
    quantity: int
    unit_amount: int
    amount_subtotal: int

    @classmethod
    def from_order_item(cls, item: OrderItem, title: str) -> "OrderItemResponse":
        return cls(
            product_id=item.product_id,
            title=title,
            quantity=item.quantity,
            unit_amount=item.unit_amount,
            amount_subtotal=item.amount_subtotal,
        )


class OrderSummaryResponse(BaseModel):
    order_id: UUID
    status: OrderStatus
    amount_total: int
    currency: str
    created_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderSummaryResponse":
        return cls(
            order_id=order.id,
            status=order.status,
            amount_total=order.amount_total,
            currency=order.currency,
            created_at=order.created_at,
        )


class OrderListResponse(BaseModel):
    orders: list[OrderSummaryResponse]


class OrderDetailResponse(BaseModel):
    order_id: UUID
    status: OrderStatus
    amount_total: int
    currency: str
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse]

    @classmethod
    def from_order(cls, order: Order, items: list[OrderItemResponse]) -> "OrderDetailResponse":
        return cls(
            order_id=order.id,
            status=order.status,
            amount_total=order.amount_total,
            currency=order.currency,
            failure_reason=order.failure_reason,
            created_at=order.created_at,
            updated_at=order.updated_at,
            items=items,
        )


class WebhookAckResponse(BaseModel):
    received: bool = True
    status: str
