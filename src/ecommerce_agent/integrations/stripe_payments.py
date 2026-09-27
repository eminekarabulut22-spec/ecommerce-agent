"""Stripe Checkout adapter (test mode only).

Everything that talks to Stripe lives behind `PaymentGateway`, so the API layer and tests never
touch the Stripe SDK directly. Card details are entered on Stripe's hosted Checkout page and
never reach this application.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol
from uuid import UUID

import stripe

from ecommerce_agent.config import get_settings

# https://docs.stripe.com/currencies#zero-decimal
ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    }
)
# https://docs.stripe.com/currencies#three-decimal - Stripe requires the last digit to be 0.
THREE_DECIMAL_CURRENCIES: frozenset[str] = frozenset({"BHD", "JOD", "KWD", "OMR", "TND"})

_TEST_KEY_PREFIXES = ("sk_test_", "rk_test_")


class PaymentConfigurationError(Exception):
    """Stripe isn't configured (or is configured with a non-test key)."""


class PaymentProviderError(Exception):
    """Stripe rejected a request or couldn't be reached."""


class WebhookVerificationError(Exception):
    """A webhook payload's signature didn't verify, or the payload isn't a valid event."""


def to_minor_units(amount: float, currency: str) -> int:
    """Convert a decimal price to the integer amount Stripe expects for `currency`."""
    code = currency.upper()
    value = Decimal(str(amount))
    if code in ZERO_DECIMAL_CURRENCIES:
        return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if code in THREE_DECIMAL_CURRENCIES:
        # Round to 2 places first so the trailing (third) digit is always 0.
        return int(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 1000)
    return int(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)


@dataclass(frozen=True)
class CheckoutSession:
    id: str
    url: str


@dataclass(frozen=True)
class CheckoutLineItem:
    product_name: str
    unit_amount: int
    quantity: int


class PaymentGateway(Protocol):
    def create_checkout_session(
        self,
        *,
        order_id: UUID,
        line_items: list[CheckoutLineItem],
        currency: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession: ...

    def construct_webhook_event(
        self, payload: bytes, signature_header: str | None
    ) -> dict[str, Any]: ...


class StripePaymentGateway:
    """Real Stripe implementation. Credentials come from `Settings`, never hardcoded."""

    def __init__(self, secret_key: str | None = None, webhook_secret: str | None = None) -> None:
        settings = get_settings()
        self._secret_key = secret_key or settings.stripe_secret_key
        self._webhook_secret = webhook_secret or settings.stripe_webhook_secret

    def _require_secret_key(self) -> str:
        if not self._secret_key:
            raise PaymentConfigurationError("STRIPE_SECRET_KEY is not configured.")
        if not self._secret_key.startswith(_TEST_KEY_PREFIXES):
            raise PaymentConfigurationError(
                "Only Stripe test-mode keys (sk_test_... / rk_test_...) are allowed."
            )
        return self._secret_key

    def create_checkout_session(
        self,
        *,
        order_id: UUID,
        line_items: list[CheckoutLineItem],
        currency: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        api_key = self._require_secret_key()
        try:
            session = stripe.checkout.Session.create(
                api_key=api_key,
                mode="payment",
                line_items=[
                    {
                        "quantity": item.quantity,
                        "price_data": {
                            "currency": currency.lower(),
                            "unit_amount": item.unit_amount,
                            "product_data": {"name": item.product_name},
                        },
                    }
                    for item in line_items
                ],
                client_reference_id=str(order_id),
                metadata={"order_id": str(order_id)},
                payment_intent_data={"metadata": {"order_id": str(order_id)}},
                success_url=success_url,
                cancel_url=cancel_url,
                # Makes a retried request (same order) return the same session, not a second one.
                idempotency_key=f"checkout-session-{order_id}",
            )
        except stripe.StripeError as exc:
            raise PaymentProviderError(exc.user_message or str(exc)) from exc
        return CheckoutSession(id=session.id, url=session.url)

    def construct_webhook_event(
        self, payload: bytes, signature_header: str | None
    ) -> dict[str, Any]:
        if not self._webhook_secret:
            raise PaymentConfigurationError("STRIPE_WEBHOOK_SECRET is not configured.")
        if not signature_header:
            raise WebhookVerificationError("Missing Stripe-Signature header.")
        try:
            stripe.WebhookSignature.verify_header(
                payload, signature_header, self._webhook_secret, stripe.Webhook.DEFAULT_TOLERANCE
            )
        except stripe.SignatureVerificationError as exc:
            raise WebhookVerificationError("Invalid Stripe webhook signature.") from exc
        try:
            event = json.loads(payload)
        except ValueError as exc:
            raise WebhookVerificationError("Webhook payload is not valid JSON.") from exc
        if not isinstance(event, dict) or "id" not in event or "type" not in event:
            raise WebhookVerificationError("Webhook payload is not a Stripe event.")
        return event
