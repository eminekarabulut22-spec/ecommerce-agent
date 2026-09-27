"""Stripe Checkout (test mode) routes.

Flow: the browser sends a cart (product ids + quantities) -> we price every item from the
database, create a pending order with its line items and one Stripe Checkout Session covering
the whole cart -> the browser is redirected to Stripe -> Stripe calls our webhook, which is the
only thing that marks an order paid/failed -> the browser comes back and polls the order status
endpoint.

Checkout-session creation (and the order-listing/detail routes below it) require a logged-in
user - orders belong to whoever created them, and a user can only ever see their own. The
webhook section further down is untouched: it only ever reads order-level totals that already
existed before carts/accounts, so none of this needed to change there.
"""

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ecommerce_agent.api.dependencies import get_current_user, get_db_session, get_payment_gateway
from ecommerce_agent.api.schemas import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    ErrorResponse,
    OrderDetailResponse,
    OrderItemResponse,
    OrderListResponse,
    OrderSummaryResponse,
    WebhookAckResponse,
)
from ecommerce_agent.config import get_settings
from ecommerce_agent.db import repository
from ecommerce_agent.integrations.stripe_payments import (
    CheckoutLineItem,
    PaymentConfigurationError,
    PaymentGateway,
    PaymentProviderError,
    WebhookVerificationError,
    to_minor_units,
)
from ecommerce_agent.models.order import Order, OrderItem, OrderStatus
from ecommerce_agent.models.product import ValidationStatus
from ecommerce_agent.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post(
    "/checkout-session",
    response_model=CheckoutSessionResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def create_checkout_session(
    body: CheckoutSessionRequest,
    session: Annotated[Session, Depends(get_db_session)],
    gateway: Annotated[PaymentGateway, Depends(get_payment_gateway)],
    user: Annotated[User, Depends(get_current_user)],
) -> CheckoutSessionResponse:
    order_items: list[OrderItem] = []
    line_items: list[CheckoutLineItem] = []
    currency: str | None = None
    amount_total = 0

    for cart_item in body.items:
        product = repository.find_by_id(session, cart_item.product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found.")
        if product.price is None or product.price <= 0 or not product.currency:
            raise HTTPException(
                status_code=409,
                detail=f"'{product.title}' has no price, so it can't be bought.",
            )
        if product.validation_status != ValidationStatus.VALID:
            raise HTTPException(
                status_code=409,
                detail=f"'{product.title}' still needs review, so it can't be bought yet.",
            )

        product_currency = product.currency.upper()
        if currency is None:
            currency = product_currency
        elif product_currency != currency:
            raise HTTPException(
                status_code=409, detail="All items in a single checkout must use the same currency."
            )

        unit_amount = to_minor_units(product.price, product_currency)
        subtotal = unit_amount * cart_item.quantity
        amount_total += subtotal
        order_items.append(
            OrderItem(
                product_id=product.id,
                quantity=cart_item.quantity,
                unit_amount=unit_amount,
                amount_subtotal=subtotal,
            )
        )
        line_items.append(
            CheckoutLineItem(
                product_name=product.title, unit_amount=unit_amount, quantity=cart_item.quantity
            )
        )

    assert currency is not None  # body.items is non-empty (schema enforces min_length=1)
    order, _ = repository.create_order_with_items(
        session,
        Order(user_id=user.id, amount_total=amount_total, currency=currency),
        order_items,
    )

    base_url = get_settings().frontend_base_url.rstrip("/")
    return_url = f"{base_url}/?order_id={order.id}"
    try:
        checkout = gateway.create_checkout_session(
            order_id=order.id,
            line_items=line_items,
            currency=currency,
            # {CHECKOUT_SESSION_ID} is a literal placeholder Stripe fills in on redirect.
            success_url=f"{return_url}&checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{return_url}&checkout=cancel",
        )
    except PaymentConfigurationError as exc:
        repository.update_order(
            session, order.id, {"status": OrderStatus.FAILED, "failure_reason": str(exc)}
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PaymentProviderError as exc:
        repository.update_order(
            session, order.id, {"status": OrderStatus.FAILED, "failure_reason": str(exc)}
        )
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc}") from exc

    repository.update_order(session, order.id, {"stripe_checkout_session_id": checkout.id})
    return CheckoutSessionResponse(order_id=order.id, checkout_url=checkout.url)


@router.get("/orders", response_model=OrderListResponse)
def list_orders(
    session: Annotated[Session, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> OrderListResponse:
    orders = repository.list_orders_for_user(session, user.id)
    return OrderListResponse(orders=[OrderSummaryResponse.from_order(o) for o in orders])


@router.get(
    "/orders/{order_id}",
    response_model=OrderDetailResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def get_order(
    order_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> OrderDetailResponse:
    order = repository.find_order_by_id(session, order_id)
    # Same 404 whether the order doesn't exist or belongs to someone else - never reveal that
    # another user's order id is valid.
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found.")
    items = [
        OrderItemResponse.from_order_item(item, title)
        for item, title in repository.list_order_items_with_product_title(session, order.id)
    ]
    return OrderDetailResponse.from_order(order, items)


@router.post(
    "/webhook",
    response_model=WebhookAckResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def stripe_webhook(
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    gateway: Annotated[PaymentGateway, Depends(get_payment_gateway)],
) -> WebhookAckResponse:
    # The signature covers the exact raw bytes, so read the body before anything parses it.
    payload = await request.body()
    try:
        event = gateway.construct_webhook_event(payload, request.headers.get("stripe-signature"))
    except PaymentConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    event_id: str = event["id"]
    event_type: str = event["type"]
    if repository.is_webhook_event_processed(session, event_id):
        return WebhookAckResponse(status="duplicate")

    obj: dict[str, Any] = (event.get("data") or {}).get("object") or {}
    order = (
        _find_order_for_checkout_session(session, obj) if event_type in _HANDLED_EVENTS else None
    )
    updates = _order_updates_for_event(event_type, obj, order) if order is not None else None

    recorded = repository.record_webhook_event(
        session,
        event_id=event_id,
        event_type=event_type,
        order_id=order.id if order is not None else None,
        order_updates=updates,
    )
    if not recorded:
        return WebhookAckResponse(status="duplicate")
    if event_type in _HANDLED_EVENTS and order is None:
        # Acknowledge anyway: an unknown order won't appear on retry, and a non-2xx would make
        # Stripe keep redelivering it.
        logger.warning("Stripe event %s (%s) did not match any order", event_id, event_type)
        return WebhookAckResponse(status="ignored")
    return WebhookAckResponse(status="processed" if updates else "ignored")


_HANDLED_EVENTS: frozenset[str] = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
    }
)


def _find_order_for_checkout_session(
    session: Session, checkout_session: dict[str, Any]
) -> Order | None:
    order_id = (checkout_session.get("metadata") or {}).get("order_id") or checkout_session.get(
        "client_reference_id"
    )
    if order_id:
        try:
            order = repository.find_order_by_id(session, UUID(str(order_id)))
        except ValueError:
            order = None
        if order is not None:
            return order
    checkout_session_id = checkout_session.get("id")
    if checkout_session_id:
        return repository.find_order_by_checkout_session_id(session, checkout_session_id)
    return None


def _order_updates_for_event(
    event_type: str, checkout_session: dict[str, Any], order: Order
) -> dict[str, Any] | None:
    """Translate a Checkout Session event into order field changes (None = nothing to change).

    A paid order is terminal: nothing that arrives later (an out-of-order `expired`, say) can
    downgrade it.
    """
    if order.status == OrderStatus.PAID:
        return None

    payment_intent = checkout_session.get("payment_intent")
    base: dict[str, Any] = {}
    if isinstance(payment_intent, str):
        base["stripe_payment_intent_id"] = payment_intent
    if not order.stripe_checkout_session_id and checkout_session.get("id"):
        base["stripe_checkout_session_id"] = checkout_session["id"]

    is_paid_event = event_type == "checkout.session.async_payment_succeeded" or (
        event_type == "checkout.session.completed"
        and checkout_session.get("payment_status") == "paid"
    )
    if is_paid_event:
        amount = checkout_session.get("amount_total")
        currency = str(checkout_session.get("currency") or "").upper()
        if amount != order.amount_total or currency != order.currency:
            return {
                **base,
                "status": OrderStatus.FAILED,
                "failure_reason": (
                    f"Stripe reported {amount} {currency}, expected "
                    f"{order.amount_total} {order.currency}."
                ),
            }
        return {**base, "status": OrderStatus.PAID, "failure_reason": None}

    if event_type == "checkout.session.async_payment_failed":
        return {**base, "status": OrderStatus.FAILED, "failure_reason": "Payment failed."}
    if event_type == "checkout.session.expired":
        return {**base, "status": OrderStatus.FAILED, "failure_reason": "Checkout session expired."}

    # checkout.session.completed with payment_status "unpaid": a delayed payment method is
    # still processing - stay pending until async_payment_succeeded/failed arrives.
    return base or None
