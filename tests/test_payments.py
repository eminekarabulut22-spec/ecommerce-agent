import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ecommerce_agent.api.dependencies import get_db_session, get_payment_gateway
from ecommerce_agent.api.main import app
from ecommerce_agent.auth import hash_password
from ecommerce_agent.db import models, repository
from ecommerce_agent.db.base import Base
from ecommerce_agent.integrations import stripe_payments
from ecommerce_agent.integrations.stripe_payments import (
    CheckoutLineItem,
    CheckoutSession,
    PaymentConfigurationError,
    PaymentProviderError,
    StripePaymentGateway,
    to_minor_units,
)
from ecommerce_agent.models.order import Order, OrderItem, OrderStatus
from ecommerce_agent.models.product import Product, ValidationStatus
from ecommerce_agent.models.user import User

WEBHOOK_SECRET = "whsec_test_secret_for_unit_tests"


class FakeGateway:
    """Records checkout requests; verifies webhooks with the real Stripe signature scheme."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self._real = StripePaymentGateway(secret_key="sk_test_fake", webhook_secret=WEBHOOK_SECRET)

    def create_checkout_session(self, **kwargs: Any) -> CheckoutSession:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return CheckoutSession(
            id=f"cs_test_{len(self.calls)}", url="https://checkout.stripe.com/c/pay/cs_test"
        )

    def construct_webhook_event(
        self, payload: bytes, signature_header: str | None
    ) -> dict[str, Any]:
        return self._real.construct_webhook_event(payload, signature_header)


@pytest.fixture()
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture()
def api(gateway: FakeGateway):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session():
        session = session_local()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_payment_gateway] = lambda: gateway
    with TestClient(app) as test_client:
        yield test_client, session_local
    app.dependency_overrides.clear()
    engine.dispose()


def _seed_product(session_local, **overrides: Any) -> Product:
    fields: dict[str, Any] = {
        "title": "Wireless Mechanical Keyboard",
        "price": 89.99,
        "currency": "USD",
        "source_image_url": "keyboard.jpg",
        "validation_status": ValidationStatus.VALID,
    }
    fields.update(overrides)
    with session_local() as session:
        return repository.save_product(session, Product(**fields))


def _seed_user(session_local, **overrides: Any) -> User:
    fields: dict[str, Any] = {
        "email": "buyer@example.com",
        "password_hash": hash_password("correct horse battery staple"),
    }
    fields.update(overrides)
    with session_local() as session:
        return repository.create_user(session, User(**fields))


def _register(client: TestClient, *, email: str = "buyer@example.com", password: str = "supersecret1") -> dict:
    """Registers a user and leaves the client holding its session cookie."""
    response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


def _seed_order(session_local, user: User, product: Product, **overrides: Any) -> Order:
    quantity = overrides.pop("quantity", 1)
    unit_amount = overrides.pop("unit_amount", 8999)
    fields: dict[str, Any] = {
        "user_id": user.id,
        "amount_total": unit_amount * quantity,
        "currency": "USD",
        "stripe_checkout_session_id": "cs_test_abc",
    }
    fields.update(overrides)
    with session_local() as session:
        order, _ = repository.create_order_with_items(
            session,
            Order(**fields),
            [
                OrderItem(
                    product_id=product.id,
                    quantity=quantity,
                    unit_amount=unit_amount,
                    amount_subtotal=unit_amount * quantity,
                )
            ],
        )
    return order


def _get_order(session_local, order_id: UUID) -> Order:
    with session_local() as session:
        order = repository.find_order_by_id(session, order_id)
    assert order is not None
    return order


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    signature = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def _event(
    event_type: str,
    order: Order,
    *,
    event_id: str | None = None,
    payment_status: str = "paid",
    amount_total: int | None = None,
    currency: str = "usd",
) -> dict[str, Any]:
    return {
        "id": event_id or f"evt_{uuid4().hex}",
        "object": "event",
        "type": event_type,
        "data": {
            "object": {
                "id": order.stripe_checkout_session_id,
                "object": "checkout.session",
                "client_reference_id": str(order.id),
                "metadata": {"order_id": str(order.id)},
                "payment_status": payment_status,
                "payment_intent": "pi_test_123",
                "amount_total": order.amount_total if amount_total is None else amount_total,
                "currency": currency,
            }
        },
    }


def _post_webhook(client: TestClient, event: dict[str, Any], *, signature: str | None = "auto"):
    payload = json.dumps(event).encode()
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers["Stripe-Signature"] = _sign(payload)
    elif signature is not None:
        headers["Stripe-Signature"] = signature
    return client.post("/payments/webhook", content=payload, headers=headers)


# --- Checkout session -------------------------------------------------------------------------


def test_checkout_session_requires_login(api, gateway: FakeGateway) -> None:
    client, session_local = api
    product = _seed_product(session_local)

    response = client.post(
        "/payments/checkout-session", json={"items": [{"product_id": str(product.id)}]}
    )

    assert response.status_code == 401
    assert gateway.calls == []


def test_checkout_session_prices_order_from_database(api, gateway: FakeGateway) -> None:
    client, session_local = api
    product = _seed_product(session_local)
    _register(client)

    response = client.post(
        "/payments/checkout-session", json={"items": [{"product_id": str(product.id)}]}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_test"

    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["currency"] == "USD"
    assert len(call["line_items"]) == 1
    line_item: CheckoutLineItem = call["line_items"][0]
    assert line_item.unit_amount == 8999
    assert line_item.quantity == 1
    assert line_item.product_name == product.title
    assert f"?order_id={body['order_id']}&checkout=success" in call["success_url"]
    assert call["success_url"].endswith("session_id={CHECKOUT_SESSION_ID}")
    assert call["cancel_url"].endswith(f"?order_id={body['order_id']}&checkout=cancel")

    order = _get_order(session_local, UUID(body["order_id"]))
    assert order.status == OrderStatus.PENDING
    assert order.amount_total == 8999
    assert order.stripe_checkout_session_id == "cs_test_1"
    with session_local() as session:
        items = repository.list_order_items(session, order.id)
    assert len(items) == 1
    assert items[0].product_id == product.id
    assert items[0].quantity == 1


def test_checkout_session_multiplies_quantity(api, gateway: FakeGateway) -> None:
    client, session_local = api
    product = _seed_product(session_local)
    _register(client)

    response = client.post(
        "/payments/checkout-session",
        json={"items": [{"product_id": str(product.id), "quantity": 3}]},
    )

    assert response.status_code == 200
    order = _get_order(session_local, UUID(response.json()["order_id"]))
    assert order.amount_total == 3 * 8999
    assert gateway.calls[0]["line_items"][0].quantity == 3


def test_checkout_session_creates_one_order_with_multiple_line_items(
    api, gateway: FakeGateway
) -> None:
    client, session_local = api
    keyboard = _seed_product(session_local, title="Keyboard", price=89.99)
    mouse = _seed_product(session_local, title="Mouse", price=29.99)
    _register(client)

    response = client.post(
        "/payments/checkout-session",
        json={
            "items": [
                {"product_id": str(keyboard.id), "quantity": 1},
                {"product_id": str(mouse.id), "quantity": 2},
            ]
        },
    )

    assert response.status_code == 200, response.text
    order = _get_order(session_local, UUID(response.json()["order_id"]))
    assert order.amount_total == 8999 + 2 * 2999
    assert len(gateway.calls[0]["line_items"]) == 2
    with session_local() as session:
        items = repository.list_order_items(session, order.id)
    assert {(i.product_id, i.quantity) for i in items} == {(keyboard.id, 1), (mouse.id, 2)}


def test_checkout_session_rejects_mixed_currencies(api, gateway: FakeGateway) -> None:
    client, session_local = api
    usd_product = _seed_product(session_local, currency="USD")
    eur_product = _seed_product(session_local, currency="EUR")
    _register(client)

    response = client.post(
        "/payments/checkout-session",
        json={
            "items": [
                {"product_id": str(usd_product.id)},
                {"product_id": str(eur_product.id)},
            ]
        },
    )

    assert response.status_code == 409
    assert gateway.calls == []


def test_checkout_session_rejects_empty_cart(api, gateway: FakeGateway) -> None:
    client, _ = api
    _register(client)

    response = client.post("/payments/checkout-session", json={"items": []})

    assert response.status_code == 422
    assert gateway.calls == []


@pytest.mark.parametrize(
    "extra",
    [{"price": 0.5}, {"amount": 1}, {"unit_amount": 1}, {"amount_total": 1}, {"currency": "JPY"}],
)
def test_checkout_session_rejects_client_supplied_pricing(api, gateway: FakeGateway, extra) -> None:
    client, session_local = api
    product = _seed_product(session_local)
    _register(client)

    response = client.post(
        "/payments/checkout-session",
        json={"items": [{"product_id": str(product.id), **extra}]},
    )

    assert response.status_code == 422
    assert gateway.calls == []


@pytest.mark.parametrize("quantity", [0, -1, 11])
def test_checkout_session_rejects_out_of_range_quantity(
    api, gateway: FakeGateway, quantity
) -> None:
    client, session_local = api
    product = _seed_product(session_local)
    _register(client)

    response = client.post(
        "/payments/checkout-session",
        json={"items": [{"product_id": str(product.id), "quantity": quantity}]},
    )

    assert response.status_code == 422
    assert gateway.calls == []


def test_checkout_session_unknown_product_is_404(api, gateway: FakeGateway) -> None:
    client, _ = api
    _register(client)

    response = client.post(
        "/payments/checkout-session", json={"items": [{"product_id": str(uuid4())}]}
    )

    assert response.status_code == 404
    assert gateway.calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": None},
        {"price": 0.0},
        {"currency": None},
        {"validation_status": ValidationStatus.NEEDS_REVIEW},
    ],
)
def test_checkout_session_refuses_unpurchasable_products(
    api, gateway: FakeGateway, overrides
) -> None:
    client, session_local = api
    product = _seed_product(session_local, **overrides)
    _register(client)

    response = client.post(
        "/payments/checkout-session", json={"items": [{"product_id": str(product.id)}]}
    )

    assert response.status_code == 409
    assert gateway.calls == []


def test_checkout_session_stripe_error_marks_order_failed(api, gateway: FakeGateway) -> None:
    client, session_local = api
    gateway.error = PaymentProviderError("Amount must be at least $0.50 usd")
    product = _seed_product(session_local, price=0.10)
    _register(client)

    response = client.post(
        "/payments/checkout-session", json={"items": [{"product_id": str(product.id)}]}
    )

    assert response.status_code == 502
    assert "Amount must be at least" in response.json()["detail"]
    with session_local() as session:
        orders = session.query(models.OrderORM).all()
    assert [o.status for o in orders] == ["failed"]


def test_checkout_session_without_stripe_config_is_503(api, gateway: FakeGateway) -> None:
    client, session_local = api
    gateway.error = PaymentConfigurationError("STRIPE_SECRET_KEY is not configured.")
    product = _seed_product(session_local)
    _register(client)

    response = client.post(
        "/payments/checkout-session", json={"items": [{"product_id": str(product.id)}]}
    )

    assert response.status_code == 503
    assert "STRIPE_SECRET_KEY" in response.json()["detail"]


# --- Order status/detail/list ------------------------------------------------------------------


def test_order_status_returns_order(api) -> None:
    client, session_local = api
    user_body = _register(client)
    with session_local() as session:
        user = repository.find_user_by_email(session, user_body["email"])
    order = _seed_order(session_local, user, _seed_product(session_local))

    response = client.get(f"/payments/orders/{order.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["order_id"] == str(order.id)
    assert body["status"] == "pending"
    assert body["amount_total"] == 8999
    assert body["currency"] == "USD"
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 1
    # Internal Stripe identifiers aren't exposed to the browser.
    assert "stripe_checkout_session_id" not in body


def test_order_status_requires_login(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    response = client.get(f"/payments/orders/{order.id}")

    assert response.status_code == 401


def test_order_status_unknown_order_is_404(api) -> None:
    client, _ = api
    _register(client)
    assert client.get(f"/payments/orders/{uuid4()}").status_code == 404


def test_order_of_another_user_is_404(api) -> None:
    client, session_local = api
    owner = _seed_user(session_local, email="owner@example.com")
    order = _seed_order(session_local, owner, _seed_product(session_local))
    _register(client, email="someone-else@example.com")

    response = client.get(f"/payments/orders/{order.id}")

    assert response.status_code == 404


def test_list_orders_returns_only_current_users_orders(api) -> None:
    client, session_local = api
    other_user = _seed_user(session_local, email="owner@example.com")
    product = _seed_product(session_local)
    _seed_order(session_local, other_user, product, stripe_checkout_session_id="cs_test_theirs")

    _register(client, email="me@example.com")
    with session_local() as session:
        me = repository.find_user_by_email(session, "me@example.com")
    mine = _seed_order(session_local, me, product, stripe_checkout_session_id="cs_test_mine")

    response = client.get("/payments/orders")

    assert response.status_code == 200
    order_ids = {o["order_id"] for o in response.json()["orders"]}
    assert order_ids == {str(mine.id)}


# --- Webhook ------------------------------------------------------------------------------------


def test_webhook_completed_marks_order_paid(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    response = _post_webhook(client, _event("checkout.session.completed", order))

    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    updated = _get_order(session_local, order.id)
    assert updated.status == OrderStatus.PAID
    assert updated.stripe_payment_intent_id == "pi_test_123"


def test_webhook_rejects_invalid_signature(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))
    event = _event("checkout.session.completed", order)
    bad_signature = _sign(json.dumps(event).encode(), secret="whsec_wrong")

    response = _post_webhook(client, event, signature=bad_signature)

    assert response.status_code == 400
    assert _get_order(session_local, order.id).status == OrderStatus.PENDING


def test_webhook_rejects_missing_signature(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    response = _post_webhook(client, _event("checkout.session.completed", order), signature=None)

    assert response.status_code == 400
    assert _get_order(session_local, order.id).status == OrderStatus.PENDING


def test_webhook_rejects_tampered_payload(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))
    original = json.dumps(_event("checkout.session.completed", order)).encode()
    tampered = original.replace(b'"amount_total": 8999', b'"amount_total": 1')

    response = client.post(
        "/payments/webhook", content=tampered, headers={"Stripe-Signature": _sign(original)}
    )

    assert response.status_code == 400
    assert _get_order(session_local, order.id).status == OrderStatus.PENDING


def test_webhook_rejects_stale_timestamp(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))
    event = _event("checkout.session.completed", order)
    stale = _sign(json.dumps(event).encode(), timestamp=int(time.time()) - 3600)

    response = _post_webhook(client, event, signature=stale)

    assert response.status_code == 400


def test_webhook_redelivery_is_idempotent(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))
    event = _event("checkout.session.completed", order, event_id="evt_same")

    first = _post_webhook(client, event)
    paid = _get_order(session_local, order.id)
    second = _post_webhook(client, event)

    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    again = _get_order(session_local, order.id)
    assert again.status == OrderStatus.PAID
    assert again.updated_at == paid.updated_at
    with session_local() as session:
        assert session.query(models.StripeWebhookEventORM).count() == 1


def test_webhook_paid_order_is_not_downgraded_by_later_expiry(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    _post_webhook(client, _event("checkout.session.completed", order))
    response = _post_webhook(client, _event("checkout.session.expired", order))

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert _get_order(session_local, order.id).status == OrderStatus.PAID


def test_webhook_amount_mismatch_marks_order_failed(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    _post_webhook(client, _event("checkout.session.completed", order, amount_total=100))

    updated = _get_order(session_local, order.id)
    assert updated.status == OrderStatus.FAILED
    assert "expected 8999 USD" in (updated.failure_reason or "")


def test_webhook_currency_mismatch_marks_order_failed(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    _post_webhook(client, _event("checkout.session.completed", order, currency="eur"))

    assert _get_order(session_local, order.id).status == OrderStatus.FAILED


def test_webhook_expired_marks_order_failed(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    _post_webhook(client, _event("checkout.session.expired", order, payment_status="unpaid"))

    updated = _get_order(session_local, order.id)
    assert updated.status == OrderStatus.FAILED
    assert updated.failure_reason == "Checkout session expired."


def test_webhook_delayed_payment_stays_pending_until_async_success(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    _post_webhook(client, _event("checkout.session.completed", order, payment_status="unpaid"))
    assert _get_order(session_local, order.id).status == OrderStatus.PENDING

    _post_webhook(client, _event("checkout.session.async_payment_succeeded", order))
    assert _get_order(session_local, order.id).status == OrderStatus.PAID


def test_webhook_async_payment_failed_marks_order_failed(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    _post_webhook(
        client, _event("checkout.session.async_payment_failed", order, payment_status="unpaid")
    )

    assert _get_order(session_local, order.id).status == OrderStatus.FAILED


def test_webhook_matches_order_by_session_id_without_metadata(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))
    event = _event("checkout.session.completed", order)
    event["data"]["object"]["metadata"] = {}
    event["data"]["object"]["client_reference_id"] = None

    _post_webhook(client, event)

    assert _get_order(session_local, order.id).status == OrderStatus.PAID


def test_webhook_unknown_order_is_acknowledged(api) -> None:
    client, _ = api
    ghost = Order(
        user_id=uuid4(),
        amount_total=1,
        currency="USD",
        stripe_checkout_session_id="cs_test_ghost",
    )

    response = _post_webhook(client, _event("checkout.session.completed", ghost))

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_unhandled_event_type_is_acknowledged(api) -> None:
    client, session_local = api
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    response = _post_webhook(client, _event("payment_intent.created", order))

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert _get_order(session_local, order.id).status == OrderStatus.PENDING


def test_webhook_without_secret_configured_is_503(api, gateway: FakeGateway) -> None:
    client, session_local = api
    gateway._real = StripePaymentGateway(secret_key="sk_test_fake", webhook_secret=None)
    gateway._real._webhook_secret = None  # ignore any STRIPE_WEBHOOK_SECRET in the local .env
    user = _seed_user(session_local)
    order = _seed_order(session_local, user, _seed_product(session_local))

    response = _post_webhook(client, _event("checkout.session.completed", order))

    assert response.status_code == 503


def test_record_webhook_event_returns_false_for_already_recorded_event(session) -> None:
    assert repository.record_webhook_event(session, event_id="evt_1", event_type="x") is True
    assert repository.record_webhook_event(session, event_id="evt_1", event_type="x") is False
    assert repository.is_webhook_event_processed(session, "evt_1")


# --- Stripe gateway ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        (89.99, "USD", 8999),
        (0.1 + 0.2, "EUR", 30),
        (19.995, "usd", 2000),
        (1500, "JPY", 1500),
        (1500.6, "KRW", 1501),
        (12.345, "KWD", 12350),
        (5, "BHD", 5000),
    ],
)
def test_to_minor_units(amount: float, currency: str, expected: int) -> None:
    assert to_minor_units(amount, currency) == expected


@pytest.mark.parametrize("key", ["sk_live_abc", "pk_test_abc", "rk_live_abc"])
def test_gateway_refuses_non_test_secret_keys(key: str) -> None:
    gateway = StripePaymentGateway(secret_key=key, webhook_secret=WEBHOOK_SECRET)
    with pytest.raises(PaymentConfigurationError):
        gateway.create_checkout_session(
            order_id=uuid4(),
            line_items=[CheckoutLineItem(product_name="x", unit_amount=100, quantity=1)],
            currency="USD",
            success_url="http://localhost/s",
            cancel_url="http://localhost/c",
        )


def test_gateway_refuses_missing_secret_key() -> None:
    gateway = StripePaymentGateway(secret_key="unused", webhook_secret=WEBHOOK_SECRET)
    gateway._secret_key = None  # ignore any STRIPE_SECRET_KEY in the local .env
    with pytest.raises(PaymentConfigurationError):
        gateway.create_checkout_session(
            order_id=uuid4(),
            line_items=[CheckoutLineItem(product_name="x", unit_amount=100, quantity=1)],
            currency="USD",
            success_url="http://localhost/s",
            cancel_url="http://localhost/c",
        )


def test_gateway_sends_expected_checkout_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        id = "cs_test_xyz"
        url = "https://checkout.stripe.com/c/pay/cs_test_xyz"

    def fake_create(**params: Any) -> _Session:
        captured.update(params)
        return _Session()

    monkeypatch.setattr(stripe_payments.stripe.checkout.Session, "create", fake_create)
    order_id = uuid4()
    gateway = StripePaymentGateway(secret_key="sk_test_123", webhook_secret=WEBHOOK_SECRET)

    result = gateway.create_checkout_session(
        order_id=order_id,
        line_items=[CheckoutLineItem(product_name="Keyboard", unit_amount=8999, quantity=2)],
        currency="USD",
        success_url="http://localhost/s",
        cancel_url="http://localhost/c",
    )

    assert result == CheckoutSession(
        id="cs_test_xyz", url="https://checkout.stripe.com/c/pay/cs_test_xyz"
    )
    assert captured["api_key"] == "sk_test_123"
    assert captured["mode"] == "payment"
    assert captured["line_items"] == [
        {
            "quantity": 2,
            "price_data": {
                "currency": "usd",
                "unit_amount": 8999,
                "product_data": {"name": "Keyboard"},
            },
        }
    ]
    assert captured["metadata"] == {"order_id": str(order_id)}
    assert captured["client_reference_id"] == str(order_id)
    assert captured["idempotency_key"] == f"checkout-session-{order_id}"


def test_gateway_sends_multiple_line_items(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Session:
        id = "cs_test_xyz"
        url = "https://checkout.stripe.com/c/pay/cs_test_xyz"

    def fake_create(**params: Any) -> _Session:
        captured.update(params)
        return _Session()

    monkeypatch.setattr(stripe_payments.stripe.checkout.Session, "create", fake_create)
    gateway = StripePaymentGateway(secret_key="sk_test_123", webhook_secret=WEBHOOK_SECRET)

    gateway.create_checkout_session(
        order_id=uuid4(),
        line_items=[
            CheckoutLineItem(product_name="Keyboard", unit_amount=8999, quantity=1),
            CheckoutLineItem(product_name="Mouse", unit_amount=2999, quantity=2),
        ],
        currency="USD",
        success_url="http://localhost/s",
        cancel_url="http://localhost/c",
    )

    assert len(captured["line_items"]) == 2
    assert captured["line_items"][1] == {
        "quantity": 2,
        "price_data": {
            "currency": "usd",
            "unit_amount": 2999,
            "product_data": {"name": "Mouse"},
        },
    }


def test_gateway_wraps_stripe_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_create(**params: Any) -> None:
        raise stripe_payments.stripe.InvalidRequestError("Invalid currency: xyz", param="currency")

    monkeypatch.setattr(stripe_payments.stripe.checkout.Session, "create", fake_create)
    gateway = StripePaymentGateway(secret_key="sk_test_123", webhook_secret=WEBHOOK_SECRET)

    with pytest.raises(PaymentProviderError, match="Invalid currency"):
        gateway.create_checkout_session(
            order_id=uuid4(),
            line_items=[CheckoutLineItem(product_name="x", unit_amount=100, quantity=1)],
            currency="XYZ",
            success_url="http://localhost/s",
            cancel_url="http://localhost/c",
        )
