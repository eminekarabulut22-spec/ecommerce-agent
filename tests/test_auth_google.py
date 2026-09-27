"""Sign in with Google. Google itself is never contacted: the code exchange and ID-token
verification in `integrations.google_oauth` are replaced with fakes, and the credentials below
are dummies."""

import base64
import hashlib
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ecommerce_agent.api import auth as auth_api
from ecommerce_agent.api.dependencies import get_db_session, get_payment_gateway
from ecommerce_agent.api.main import app
from ecommerce_agent.auth import SESSION_COOKIE_NAME, hash_password
from ecommerce_agent.config import get_settings
from ecommerce_agent.db import repository
from ecommerce_agent.db.base import Base
from ecommerce_agent.integrations import google_oauth
from ecommerce_agent.integrations.google_oauth import GoogleIdentity, GoogleOAuthError
from ecommerce_agent.integrations.stripe_payments import CheckoutSession
from ecommerce_agent.models.product import Product, ValidationStatus
from ecommerce_agent.models.user import User

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret-not-real"
BASE_URL = "http://testserver"
REDIRECT_URI = f"{BASE_URL}/auth/google/callback"


class FakeGoogle:
    """Stands in for Google's token endpoint + ID-token verification."""

    def __init__(self) -> None:
        self.identity: dict[str, Any] = {
            "sub": "google-sub-123",
            "email": "Shopper@Gmail.com",
            "email_verified": True,
        }
        self.nonce_override: str | None = None
        self.exchange_error: Exception | None = None
        self.verify_error: Exception | None = None
        self.exchange_calls: list[dict[str, Any]] = []
        self.verify_calls: list[dict[str, Any]] = []
        self._last_nonce: str | None = None

    def exchange_code(self, **kwargs: Any) -> str:
        self.exchange_calls.append(kwargs)
        if self.exchange_error is not None:
            raise self.exchange_error
        return "fake.id.token"

    def verify_id_token(self, token: str, *, client_id: str) -> GoogleIdentity:
        self.verify_calls.append({"token": token, "client_id": client_id})
        if self.verify_error is not None:
            raise self.verify_error
        nonce = self.nonce_override if self.nonce_override is not None else self._last_nonce
        return GoogleIdentity(nonce=nonce, **self.identity)


@pytest.fixture()
def fake_google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    fake = FakeGoogle()
    real_build = google_oauth.build_authorization_url

    def build(**kwargs: Any) -> str:
        fake._last_nonce = kwargs["nonce"]  # what a real Google would echo back in the ID token
        return real_build(**kwargs)

    monkeypatch.setattr(google_oauth, "build_authorization_url", build)
    monkeypatch.setattr(google_oauth, "exchange_code", fake.exchange_code)
    monkeypatch.setattr(google_oauth, "verify_id_token", fake.verify_id_token)
    return fake


@pytest.fixture()
def google_configured(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_oauth_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_oauth_client_secret", CLIENT_SECRET)
    monkeypatch.setattr(settings, "frontend_base_url", BASE_URL)


@pytest.fixture()
def api(google_configured, fake_google):
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
    with TestClient(app, base_url=BASE_URL) as test_client:
        yield test_client, session_local
    app.dependency_overrides.clear()
    engine.dispose()


def _start(client: TestClient, next_key: str | None = None) -> dict[str, list[str]]:
    """Hits /auth/google/login and returns the query params of the Google redirect."""
    url = "/auth/google/login" + (f"?next={next_key}" if next_key else "")
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 303, response.text
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        google_oauth.AUTHORIZATION_ENDPOINT
    )
    return parse_qs(location.query)


def _callback(client: TestClient, *, state: str, code: str = "auth-code-xyz", **extra: str):
    params = {"state": state, "code": code, **extra}
    return client.get("/auth/google/callback", params=params, follow_redirects=False)


def _sign_in(client: TestClient, next_key: str | None = None):
    params = _start(client, next_key)
    return _callback(client, state=params["state"][0])


def _find_user(session_local, email: str) -> User | None:
    with session_local() as session:
        return repository.find_user_by_email(session, email)


def _seed_password_user(session_local, email: str = "shopper@gmail.com") -> User:
    with session_local() as session:
        return repository.create_user(
            session, User(email=email, password_hash=hash_password("supersecret1"))
        )


def _assert_failed(response, *, next_key: str | None = None) -> None:
    assert response.status_code == 303
    expected = "/account.html?error=google" + (f"&next={next_key}" if next_key else "")
    assert response.headers["location"] == expected
    assert SESSION_COOKIE_NAME not in response.cookies


# --- Starting the flow ---------------------------------------------------------------------


def test_providers_reports_google_enabled(api) -> None:
    client, _ = api
    assert client.get("/auth/providers").json() == {"google": True}


def test_providers_reports_google_disabled_without_credentials(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    monkeypatch.setattr(get_settings(), "google_oauth_client_secret", None)
    assert client.get("/auth/providers").json() == {"google": False}


def test_login_returns_503_when_not_configured(api, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    monkeypatch.setattr(get_settings(), "google_oauth_client_id", None)

    response = client.get("/auth/google/login", follow_redirects=False)

    assert response.status_code == 503


def test_login_redirects_to_google_with_state_nonce_and_pkce(api) -> None:
    client, _ = api

    params = _start(client, "cart")

    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [REDIRECT_URI]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid email"]
    assert params["code_challenge_method"] == ["S256"]
    assert "client_secret" not in params
    flow_cookie = client.cookies.get(auth_api.GOOGLE_FLOW_COOKIE_NAME)
    assert flow_cookie
    flow = json.loads(base64.urlsafe_b64decode(flow_cookie))
    assert params["state"] == [flow["state"]]
    assert params["nonce"] == [flow["nonce"]]
    assert flow["next"] == "cart"
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert params["code_challenge"] == [expected_challenge]


def test_login_generates_fresh_state_each_time(api) -> None:
    client, _ = api
    assert _start(client)["state"] != _start(client)["state"]


@pytest.mark.parametrize(("base_url", "secure"), [("https://demo.example.com", True), (BASE_URL, False)])
def test_flow_cookie_secure_flag_follows_https(
    api, monkeypatch: pytest.MonkeyPatch, base_url: str, secure: bool
) -> None:
    client, _ = api
    monkeypatch.setattr(get_settings(), "frontend_base_url", base_url)

    header = client.get("/auth/google/login", follow_redirects=False).headers["set-cookie"]

    assert header.startswith(f"{auth_api.GOOGLE_FLOW_COOKIE_NAME}=")
    assert ("; secure" in header.lower()) is secure
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()


# --- Callback: happy paths -----------------------------------------------------------------


def test_new_google_user_is_created_without_password_and_logged_in(
    api, fake_google: FakeGoogle
) -> None:
    client, session_local = api

    response = _sign_in(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE_NAME in response.cookies
    user = _find_user(session_local, "shopper@gmail.com")  # lowercased
    assert user is not None
    assert user.password_hash is None
    assert user.google_sub == "google-sub-123"
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "shopper@gmail.com"
    # The flow cookie is single-use.
    assert client.cookies.get(auth_api.GOOGLE_FLOW_COOKIE_NAME) is None
    # Code exchange used our secret + PKCE verifier; token verified against our client ID.
    exchange = fake_google.exchange_calls[0]
    assert exchange["code"] == "auth-code-xyz"
    assert exchange["client_secret"] == CLIENT_SECRET
    assert exchange["redirect_uri"] == REDIRECT_URI
    assert exchange["code_verifier"]
    assert fake_google.verify_calls == [{"token": "fake.id.token", "client_id": CLIENT_ID}]


def test_existing_password_user_is_linked_by_verified_email(api) -> None:
    client, session_local = api
    existing = _seed_password_user(session_local)

    response = _sign_in(client)

    assert response.status_code == 303
    assert client.get("/auth/me").json()["id"] == str(existing.id)
    linked = _find_user(session_local, "shopper@gmail.com")
    assert linked.google_sub == "google-sub-123"
    assert linked.password_hash == existing.password_hash
    # Password login keeps working for the linked account.
    client.post("/auth/logout")
    login = client.post(
        "/auth/login", json={"email": "shopper@gmail.com", "password": "supersecret1"}
    )
    assert login.status_code == 200


def test_returning_google_user_is_matched_by_sub_even_if_email_changed(
    api, fake_google: FakeGoogle
) -> None:
    client, session_local = api
    _sign_in(client)
    first_id = client.get("/auth/me").json()["id"]
    client.post("/auth/logout")

    fake_google.identity["email"] = "renamed@gmail.com"
    response = _sign_in(client)

    assert response.status_code == 303
    assert client.get("/auth/me").json()["id"] == first_id
    assert _find_user(session_local, "renamed@gmail.com") is None


@pytest.mark.parametrize(
    ("next_key", "expected"),
    [("cart", "/cart.html"), ("orders", "/orders.html"), (None, "/")],
)
def test_callback_redirects_to_allowed_next_page(api, next_key, expected) -> None:
    client, _ = api
    assert _sign_in(client, next_key).headers["location"] == expected


@pytest.mark.parametrize(
    "next_key", ["https://evil.example.com", "//evil.example.com", "/admin", "account"]
)
def test_callback_ignores_unsafe_next(api, next_key) -> None:
    client, _ = api
    response = _sign_in(client, next_key)
    assert response.headers["location"] == "/"


# --- Callback: rejected ---------------------------------------------------------------------


def test_callback_rejects_state_mismatch(api) -> None:
    client, session_local = api
    _start(client, "cart")

    response = _callback(client, state="attacker-state")

    _assert_failed(response, next_key="cart")
    assert _find_user(session_local, "shopper@gmail.com") is None


def test_callback_rejects_missing_flow_cookie(api) -> None:
    client, _ = api
    state = _start(client)["state"][0]
    client.cookies.clear()

    _assert_failed(_callback(client, state=state))


def test_callback_rejects_malformed_flow_cookie(api) -> None:
    client, _ = api
    client.cookies.set(auth_api.GOOGLE_FLOW_COOKIE_NAME, "not-base64-json", path="/auth/google")

    _assert_failed(_callback(client, state="anything"))


def test_callback_cannot_be_replayed(api) -> None:
    client, _ = api
    state = _start(client)["state"][0]
    assert SESSION_COOKIE_NAME in _callback(client, state=state).cookies
    client.post("/auth/logout")

    _assert_failed(_callback(client, state=state))


def test_callback_rejects_google_error_param(api, fake_google: FakeGoogle) -> None:
    client, _ = api
    state = _start(client)["state"][0]

    response = client.get(
        "/auth/google/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )

    _assert_failed(response)
    assert fake_google.exchange_calls == []


def test_callback_rejects_failed_code_exchange(api, fake_google: FakeGoogle) -> None:
    client, _ = api
    fake_google.exchange_error = GoogleOAuthError("Token endpoint returned HTTP 400")
    _assert_failed(_sign_in(client))


def test_callback_rejects_invalid_id_token(api, fake_google: FakeGoogle) -> None:
    client, _ = api
    fake_google.verify_error = GoogleOAuthError("Invalid ID token: wrong audience")
    _assert_failed(_sign_in(client))


def test_callback_rejects_nonce_mismatch(api, fake_google: FakeGoogle) -> None:
    client, session_local = api
    fake_google.nonce_override = "some-other-nonce"

    _assert_failed(_sign_in(client))
    assert _find_user(session_local, "shopper@gmail.com") is None


def test_callback_rejects_unverified_email(api, fake_google: FakeGoogle) -> None:
    client, session_local = api
    _seed_password_user(session_local)
    fake_google.identity["email_verified"] = False

    _assert_failed(_sign_in(client))
    assert _find_user(session_local, "shopper@gmail.com").google_sub is None


def test_callback_rejects_missing_email(api, fake_google: FakeGoogle) -> None:
    client, _ = api
    fake_google.identity["email"] = None
    _assert_failed(_sign_in(client))


def test_callback_rejects_email_owned_by_a_different_google_account(
    api, fake_google: FakeGoogle
) -> None:
    client, session_local = api
    with session_local() as session:
        repository.create_user(
            session, User(email="shopper@gmail.com", google_sub="another-google-sub")
        )

    _assert_failed(_sign_in(client))


# --- Google-only users and the rest of the app ---------------------------------------------


def test_password_login_for_google_only_user_is_a_clean_401(api) -> None:
    client, _ = api
    _sign_in(client)
    client.post("/auth/logout")

    for password in ("", "supersecret1", "x" * 200):
        response = client.post(
            "/auth/login", json={"email": "shopper@gmail.com", "password": password}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect email or password."


def test_register_with_google_only_email_is_409(api) -> None:
    client, _ = api
    _sign_in(client)
    client.post("/auth/logout")

    response = client.post(
        "/auth/register", json={"email": "shopper@gmail.com", "password": "supersecret1"}
    )

    assert response.status_code == 409


def test_google_user_can_checkout_and_list_orders(api) -> None:
    client, session_local = api
    checkout_calls: list[dict[str, Any]] = []

    class FakeGateway:
        def create_checkout_session(self, **kwargs: Any) -> CheckoutSession:
            checkout_calls.append(kwargs)
            return CheckoutSession(id="cs_test_google", url="https://checkout.stripe.com/c/pay/x")

    app.dependency_overrides[get_payment_gateway] = lambda: FakeGateway()
    with session_local() as session:
        product = repository.save_product(
            session,
            Product(
                title="Keyboard",
                price=89.99,
                currency="USD",
                source_image_url="keyboard.jpg",
                validation_status=ValidationStatus.VALID,
            ),
        )
    _sign_in(client)

    checkout = client.post(
        "/payments/checkout-session",
        json={"items": [{"product_id": str(product.id), "quantity": 2}]},
    )

    assert checkout.status_code == 200, checkout.text
    order_id = checkout.json()["order_id"]
    assert len(checkout_calls) == 1
    orders = client.get("/payments/orders")
    assert orders.status_code == 200
    assert [o["order_id"] for o in orders.json()["orders"]] == [order_id]
    assert client.get(f"/payments/orders/{order_id}").status_code == 200


def test_logout_ends_google_session(api) -> None:
    client, _ = api
    _sign_in(client)
    assert client.get("/auth/me").status_code == 200

    client.post("/auth/logout")

    assert client.get("/auth/me").status_code == 401


# --- integrations.google_oauth (the real functions, with HTTP/crypto mocked) ----------------


def test_build_authorization_url_contains_required_params() -> None:
    url = google_oauth.build_authorization_url(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        state="s",
        nonce="n",
        code_challenge="c",
    )
    params = parse_qs(urlparse(url).query)
    assert params["state"] == ["s"]
    assert params["nonce"] == ["n"]
    assert params["code_challenge"] == ["c"]
    assert params["code_challenge_method"] == ["S256"]


def test_generate_pkce_pair_is_s256() -> None:
    verifier, challenge = google_oauth.generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _mock_token_endpoint(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> list:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(google_oauth.httpx, "post", fake_post)
    return calls


def test_exchange_code_posts_to_google_token_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_token_endpoint(
        monkeypatch, httpx.Response(200, json={"id_token": "raw.jwt", "access_token": "ignored"})
    )

    token = google_oauth.exchange_code(
        code="abc",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        code_verifier="verifier",
    )

    assert token == "raw.jwt"
    assert calls[0]["url"] == google_oauth.TOKEN_ENDPOINT
    sent = calls[0]["data"]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "abc"
    assert sent["code_verifier"] == "verifier"
    assert sent["redirect_uri"] == REDIRECT_URI


@pytest.mark.parametrize(
    "response",
    [httpx.Response(400, json={"error": "invalid_grant"}), httpx.Response(200, json={})],
)
def test_exchange_code_raises_on_bad_response(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response
) -> None:
    _mock_token_endpoint(monkeypatch, response)
    with pytest.raises(GoogleOAuthError):
        google_oauth.exchange_code(
            code="abc",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            code_verifier="v",
        )


def test_exchange_code_raises_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(google_oauth.httpx, "post", fail)
    with pytest.raises(GoogleOAuthError):
        google_oauth.exchange_code(
            code="abc",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            code_verifier="v",
        )


def test_verify_id_token_checks_audience_and_maps_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_verify(token, request, audience=None, **kwargs):
        seen.update(token=token, audience=audience)
        return {"sub": "123", "email": "a@b.com", "email_verified": True, "nonce": "n"}

    monkeypatch.setattr(google_oauth.google_id_token, "verify_oauth2_token", fake_verify)

    identity = google_oauth.verify_id_token("raw.jwt", client_id=CLIENT_ID)

    assert seen == {"token": "raw.jwt", "audience": CLIENT_ID}
    assert identity == GoogleIdentity(sub="123", email="a@b.com", email_verified=True, nonce="n")


@pytest.mark.parametrize("email_verified", ["true", None, False])
def test_verify_id_token_only_accepts_boolean_true_email_verified(
    monkeypatch: pytest.MonkeyPatch, email_verified
) -> None:
    monkeypatch.setattr(
        google_oauth.google_id_token,
        "verify_oauth2_token",
        lambda *a, **k: {"sub": "1", "email": "a@b.com", "email_verified": email_verified},
    )
    assert google_oauth.verify_id_token("t", client_id=CLIENT_ID).email_verified is False


def test_verify_id_token_wraps_verification_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_verify(*args, **kwargs):
        raise ValueError("Token has wrong audience")

    monkeypatch.setattr(google_oauth.google_id_token, "verify_oauth2_token", fake_verify)

    with pytest.raises(GoogleOAuthError):
        google_oauth.verify_id_token("raw.jwt", client_id=CLIENT_ID)
