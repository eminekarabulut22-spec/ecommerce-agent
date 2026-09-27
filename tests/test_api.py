from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ecommerce_agent.api.dependencies import get_db_session, get_llm_client, get_search_provider
from ecommerce_agent.api.main import app
from ecommerce_agent.db import models, repository  # noqa: F401  (models registers ProductORM)
from ecommerce_agent.db.base import Base
from ecommerce_agent.llm.client import AgentToolUse, AgentTurnResult, LLMClientError, ProductSearchResult
from ecommerce_agent.models.product import Product, ValidationStatus


class FakeAgentLLMClient:
    """Test double for LLMClient: scripted extraction payload + scripted agent-loop turns."""

    def __init__(
        self,
        *,
        extraction_payload: dict[str, Any] | None = None,
        extraction_error: Exception | None = None,
        turns: list[tuple[str, dict[str, Any]] | None] | None = None,
    ) -> None:
        self._extraction_payload = extraction_payload
        self._extraction_error = extraction_error
        self._turns = turns or []
        self._turn_count = 0

    def call_structured_tool(self, **kwargs: Any) -> dict[str, Any]:
        if self._extraction_error is not None:
            raise self._extraction_error
        assert self._extraction_payload is not None
        return self._extraction_payload

    def send_agent_turn(
        self, *, system_prompt: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AgentTurnResult:
        idx = self._turn_count
        self._turn_count += 1
        assert idx < len(self._turns), f"ran out of scripted turns at index {idx}"
        step = self._turns[idx]
        tool_name, tool_args = step
        tool_use_id = f"toolu_{idx}"
        return AgentTurnResult(
            stop_reason="tool_use",
            assistant_content=[
                {"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": tool_args}
            ],
            tool_use=AgentToolUse(id=tool_use_id, name=tool_name, input=tool_args),
            text=None,
        )


class FakeSearchProvider:
    def __init__(self, *, responses: dict[str, list[ProductSearchResult]] | None = None) -> None:
        self._responses = responses or {}

    def search_product_field(
        self, *, product_title: str, field: str, context: dict[str, Any] | None = None
    ) -> list[ProductSearchResult]:
        return self._responses.get(field, [])


def _raise_missing_credentials() -> Any:
    """Simulates the get_llm_client dependency provider itself failing - e.g. the real
    AnthropicLLMClient() constructor raising because no API key is configured. This is
    distinct from a client whose *methods* raise: ProductAgent.run() already catches that
    case internally and reports it as outcome=EXTRACTION_FAILED, not an HTTP error."""
    raise LLMClientError("No Anthropic API key configured.")


FULL_VALID_PAYLOAD = {
    "title": {"value": "Wireless Mechanical Keyboard", "confidence": 0.95},
    "category": {"value": "Electronics > Keyboards", "confidence": 0.9},
    "gtin": {"value": "00012345678905", "confidence": 0.9},
}


def _in_memory_db_override(*, create_tables: bool = True):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    if create_tables:
        Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, expire_on_commit=False)

    def override() -> Any:
        session = session_local()
        try:
            yield session
        finally:
            session.close()

    return override, engine, session_local


def _post_image(
    client: TestClient,
    *,
    filename: str = "product.jpg",
    content_type: str | None = "image/jpeg",
    content: bytes = b"fake-image-bytes",
    price: float | None = 89.99,
    currency: str | None = "USD",
):
    data = {}
    if price is not None:
        data["price"] = str(price)
    if currency is not None:
        data["currency"] = currency
    return client.post(
        "/products/process",
        files={"image": (filename, content, content_type)},
        data=data or None,
    )


@pytest.fixture()
def client():
    override, engine, _session_local = _in_memory_db_override()
    app.dependency_overrides[get_db_session] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture()
def client_and_db():
    """Like `client`, but also exposes a session factory so a test can seed rows directly
    against the same in-memory database the API is using."""
    override, engine, session_local = _in_memory_db_override()
    app.dependency_overrides[get_db_session] = override
    with TestClient(app) as test_client:
        yield test_client, session_local
    app.dependency_overrides.clear()
    engine.dispose()


def test_health_check(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_process_product_success(client: TestClient) -> None:
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient(
        extraction_payload=FULL_VALID_PAYLOAD,
        turns=[("check_duplicate_product", {}), ("validate_product", {}), ("save_product", {})],
    )
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "saved"
    assert body["product"]["title"] == "Wireless Mechanical Keyboard"
    assert body["product"]["price"] == 89.99
    assert body["product"]["currency"] == "USD"
    assert body["product"]["confidence_scores"]["price"] == 1.0
    assert body["human_review"] is None
    assert body["trace"]["tool_calls"][0]["tool_name"] == "extract_product_attributes"


def test_process_product_uses_request_price_not_extracted_price(client: TestClient) -> None:
    payload_with_ignored_price = {
        **FULL_VALID_PAYLOAD,
        "price": {"value": 1.23, "confidence": 0.99},
        "currency": {"value": "EUR", "confidence": 0.99},
    }
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient(
        extraction_payload=payload_with_ignored_price,
        turns=[("check_duplicate_product", {}), ("validate_product", {}), ("save_product", {})],
    )
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client, price=42.5, currency="gbp")

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "saved"
    assert body["product"]["price"] == 42.5
    assert body["product"]["currency"] == "GBP"


def test_process_product_requires_price_and_currency(client: TestClient) -> None:
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient()
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client, price=None, currency=None)

    assert response.status_code == 422


def test_process_product_rejects_invalid_currency(client: TestClient) -> None:
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient()
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client, currency="US")

    assert response.status_code == 400
    assert "3-letter" in response.json()["detail"]


def test_process_product_extraction_failure(client: TestClient) -> None:
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient(
        extraction_error=LLMClientError("vision model unavailable")
    )
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client)

    assert response.status_code == 200  # the endpoint completed; the agent reported failure
    body = response.json()
    assert body["outcome"] == "extraction_failed"
    assert body["product"] is None


def test_process_product_needs_human_review(client: TestClient) -> None:
    bad_gtin_payload = {**FULL_VALID_PAYLOAD, "gtin": {"value": "00012345678900", "confidence": 0.9}}
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient(
        extraction_payload=bad_gtin_payload,
        turns=[("validate_product", {}), ("flag_for_human_review", {"reason": "bad GTIN checksum"})],
    )
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "needs_review"
    assert body["human_review"] is not None
    assert any("checksum" in e for e in body["human_review"]["validation_errors"])


def test_process_product_rejects_unsupported_content_type(client: TestClient) -> None:
    # Harmless fakes so the DI graph resolves - upload validation runs in the route body,
    # so it must short-circuit with 400 before these would ever actually be called.
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient()
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client, content_type="text/plain", content=b"not an image")

    assert response.status_code == 400
    assert "Unsupported image type" in response.json()["detail"]


def test_process_product_rejects_empty_file(client: TestClient) -> None:
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient()
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client, content=b"")

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_process_product_llm_client_failure_returns_502(client: TestClient) -> None:
    app.dependency_overrides[get_llm_client] = _raise_missing_credentials
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    response = _post_image(client)

    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "llm_error"


def test_process_product_database_error_returns_503() -> None:
    override, engine, _session_local = _in_memory_db_override(create_tables=False)  # no tables -> queries fail
    app.dependency_overrides[get_db_session] = override
    app.dependency_overrides[get_llm_client] = lambda: FakeAgentLLMClient(
        extraction_payload=FULL_VALID_PAYLOAD,
        turns=[("check_duplicate_product", {})],
    )
    app.dependency_overrides[get_search_provider] = lambda: FakeSearchProvider()

    with TestClient(app) as test_client:
        response = _post_image(test_client)

    app.dependency_overrides.clear()
    engine.dispose()

    assert response.status_code == 503
    assert response.json()["error"] == "database_error"



@pytest.mark.parametrize("app_env", ["production", "development"])
def test_database_error_detail_is_generic_outside_development(
    monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    from ecommerce_agent.config import get_settings

    monkeypatch.setattr(get_settings(), "app_env", app_env)
    override, engine, _session_local = _in_memory_db_override(create_tables=False)
    app.dependency_overrides[get_db_session] = override

    with TestClient(app) as test_client:
        response = test_client.get("/products")

    app.dependency_overrides.clear()
    engine.dispose()

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "database_error"
    if app_env == "production":
        assert body["detail"] == "Database unavailable."
    else:
        assert "no such table" in body["detail"]  # raw detail still shown locally


# --- GET /products ---------------------------------------------------------------------------


def _demo_product() -> Product:
    return Product(
        title="Demo Bicycle",
        category="Sporting Goods > Cycling",
        price=249.0,
        currency="USD",
        tags=["demo", "sample-data", "bicycle"],
        source_image_url="data/sample_images/bisiklet.jpeg",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        validation_status=ValidationStatus.VALID,
    )


def _agent_product() -> Product:
    return Product(
        title="MAC Lipstick",
        category="Beauty > Makeup",
        brand="MAC",
        price=24.0,
        currency="USD",
        tags=["lipstick", "makeup"],
        source_image_url="macruj.jpeg",  # bare filename, as a real upload produces
        extraction_model="claude-sonnet-5",
        confidence_scores={"title": 0.9, "brand": 0.7},
        created_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        validation_status=ValidationStatus.VALID,
    )


def test_list_products_empty(client: TestClient) -> None:
    response = client.get("/products")
    assert response.status_code == 200
    assert response.json() == {"products": [], "count": 0}


def test_list_products_resolves_image_url_and_demo_flag(client_and_db) -> None:
    test_client, session_local = client_and_db
    session = session_local()
    try:
        repository.save_product(session, _demo_product())
        repository.save_product(session, _agent_product())
    finally:
        session.close()

    response = test_client.get("/products")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert [p["title"] for p in body["products"]] == ["MAC Lipstick", "Demo Bicycle"]  # newest first

    agent_item = body["products"][0]
    assert agent_item["is_demo"] is False
    assert agent_item["image_url"] == "/sample-images/macruj.jpeg"
    assert agent_item["extraction_model"] == "claude-sonnet-5"
    assert agent_item["confidence_scores"] == {"title": 0.9, "brand": 0.7}

    demo_item = body["products"][1]
    assert demo_item["is_demo"] is True
    assert demo_item["image_url"] == "/sample-images/bisiklet.jpeg"
    assert demo_item["extraction_model"] is None
    assert demo_item["price"] == 249.0
    assert demo_item["currency"] == "USD"


def test_list_products_respects_limit(client_and_db) -> None:
    test_client, session_local = client_and_db
    session = session_local()
    try:
        for i in range(3):
            product = _demo_product().model_copy(
                update={
                    "id": uuid4(),
                    "title": f"Item {i}",
                    "created_at": datetime(2024, 1, i + 1, tzinfo=timezone.utc),
                }
            )
            repository.save_product(session, product)
    finally:
        session.close()

    response = test_client.get("/products?limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["products"]) == 2
