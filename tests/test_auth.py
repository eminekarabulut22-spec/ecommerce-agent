import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ecommerce_agent.api.dependencies import get_db_session
from ecommerce_agent.api.main import app
from ecommerce_agent.auth import SESSION_COOKIE_NAME, hash_password, verify_password
from ecommerce_agent.db.base import Base


@pytest.fixture()
def api():
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def test_register_creates_account_and_logs_in(api: TestClient) -> None:
    response = api.post("/auth/register", json={"email": "New@Example.com", "password": "supersecret1"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "new@example.com"  # normalized to lowercase
    assert "password" not in body
    assert "password_hash" not in body
    assert SESSION_COOKIE_NAME in response.cookies

    me = api.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "new@example.com"


def test_register_rejects_duplicate_email(api: TestClient) -> None:
    api.post("/auth/register", json={"email": "dup@example.com", "password": "supersecret1"})

    response = api.post("/auth/register", json={"email": "dup@example.com", "password": "anotherpass1"})

    assert response.status_code == 409


def test_register_rejects_short_password(api: TestClient) -> None:
    response = api.post("/auth/register", json={"email": "short@example.com", "password": "short"})
    assert response.status_code == 422


def test_register_rejects_invalid_email(api: TestClient) -> None:
    response = api.post("/auth/register", json={"email": "not-an-email", "password": "supersecret1"})
    assert response.status_code == 422


def test_register_rejects_password_over_bcrypts_72_byte_limit(api: TestClient) -> None:
    response = api.post("/auth/register", json={"email": "long@example.com", "password": "a" * 100})
    assert response.status_code == 422


def test_login_succeeds_with_correct_credentials(api: TestClient) -> None:
    api.post("/auth/register", json={"email": "user@example.com", "password": "supersecret1"})
    api.post("/auth/logout")

    response = api.post("/auth/login", json={"email": "user@example.com", "password": "supersecret1"})

    assert response.status_code == 200
    assert api.get("/auth/me").status_code == 200


def test_login_rejects_wrong_password(api: TestClient) -> None:
    api.post("/auth/register", json={"email": "user@example.com", "password": "supersecret1"})
    api.post("/auth/logout")

    response = api.post("/auth/login", json={"email": "user@example.com", "password": "wrong-password"})

    assert response.status_code == 401


def test_login_rejects_unknown_email(api: TestClient) -> None:
    response = api.post("/auth/login", json={"email": "ghost@example.com", "password": "whatever1"})
    assert response.status_code == 401


def test_me_requires_login(api: TestClient) -> None:
    assert api.get("/auth/me").status_code == 401


def test_logout_invalidates_session(api: TestClient) -> None:
    api.post("/auth/register", json={"email": "user@example.com", "password": "supersecret1"})
    assert api.get("/auth/me").status_code == 200

    api.post("/auth/logout")

    assert api.get("/auth/me").status_code == 401


def test_passwords_are_hashed_not_stored_in_plaintext() -> None:
    hashed = hash_password("supersecret1")
    assert hashed != "supersecret1"
    assert hashed.startswith("$2b$") or hashed.startswith("$2a$")
    assert verify_password("supersecret1", hashed) is True
    assert verify_password("wrong", hashed) is False


@pytest.mark.parametrize(("base_url", "secure"), [("https://demo.example.com", True), ("http://localhost:8000", False)])
def test_session_cookie_secure_flag_follows_https(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, base_url: str, secure: bool
) -> None:
    from ecommerce_agent.config import get_settings

    monkeypatch.setattr(get_settings(), "frontend_base_url", base_url)

    login_cookie = api.post(
        "/auth/register", json={"email": "secure@example.com", "password": "supersecret1"}
    ).headers["set-cookie"]
    logout_cookie = api.post("/auth/logout").headers["set-cookie"]

    for header in (login_cookie, logout_cookie):  # the deletion must match the cookie it clears
        assert header.startswith(f"{SESSION_COOKIE_NAME}=")
        assert ("; secure" in header.lower()) is secure
        assert "samesite=lax" in header.lower()
    assert "httponly" in login_cookie.lower()
