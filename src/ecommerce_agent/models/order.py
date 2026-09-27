from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class Order(BaseModel):
    """A cart's worth of products, paid through a single Stripe Checkout Session (test mode).

    `amount_total`/`currency` are the order-level totals Stripe's webhook verifies against -
    always computed from the database, never taken from the client. The individual products are
    `OrderItem` rows (one order can hold several).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    amount_total: int = Field(ge=0)
    currency: str
    status: OrderStatus = OrderStatus.PENDING
    stripe_checkout_session_id: str | None = None
    stripe_payment_intent_id: str | None = None
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class OrderItem(BaseModel):
    """One product/quantity line within an `Order`, priced from the database at checkout time."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    # None until inserted: the repository fills this in with the newly created order's id.
    order_id: UUID | None = None
    product_id: UUID
    quantity: int = Field(ge=1)
    unit_amount: int = Field(ge=0)
    amount_subtotal: int = Field(ge=0)
