from typing import Any

from sqlalchemy.orm import Session

from ecommerce_agent.agent.orchestrator import AgentOutcome, ProductAgent
from ecommerce_agent.db import repository
from ecommerce_agent.llm.client import AgentToolUse, AgentTurnResult, LLMClientError, ProductSearchResult
from ecommerce_agent.models.product import Product, ValidationStatus


class FakeAgentLLMClient:
    """Test double for LLMClient: a scripted extraction payload plus a scripted sequence of
    agent-loop tool-use decisions, so tests can pin down exactly what "the LLM" chooses to do
    each turn without ever calling a real model."""

    def __init__(
        self,
        *,
        extraction_payload: dict[str, Any] | None = None,
        extraction_error: Exception | None = None,
        turns: list[tuple[str, dict[str, Any]] | None] | None = None,
        error_at_turn: int | None = None,
    ) -> None:
        self._extraction_payload = extraction_payload
        self._extraction_error = extraction_error
        self._turns = turns or []
        self._error_at_turn = error_at_turn
        self.turn_count = 0

    def call_structured_tool(self, **kwargs: Any) -> dict[str, Any]:
        if self._extraction_error is not None:
            raise self._extraction_error
        assert self._extraction_payload is not None
        return self._extraction_payload

    def send_agent_turn(
        self, *, system_prompt: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AgentTurnResult:
        idx = self.turn_count
        self.turn_count += 1

        if self._error_at_turn is not None and idx == self._error_at_turn:
            raise LLMClientError("simulated agent decision API failure")

        assert idx < len(self._turns), f"FakeAgentLLMClient ran out of scripted turns at index {idx}"
        step = self._turns[idx]

        if step is None:
            return AgentTurnResult(
                stop_reason="end_turn",
                assistant_content=[{"type": "text", "text": "Thinking..."}],
                tool_use=None,
                text="Thinking...",
            )

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
    def __init__(
        self,
        *,
        responses: dict[str, list[ProductSearchResult]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._responses = responses or {}
        self._error = error
        self.calls: list[str] = []

    def search_product_field(
        self, *, product_title: str, field: str, context: dict[str, Any] | None = None
    ) -> list[ProductSearchResult]:
        self.calls.append(field)
        if self._error is not None:
            raise self._error
        return self._responses.get(field, [])


FULL_VALID_PAYLOAD = {
    "title": {"value": "Wireless Mechanical Keyboard", "confidence": 0.95},
    "category": {"value": "Electronics > Keyboards", "confidence": 0.9},
    "price": {"value": 89.99, "confidence": 0.85},
    "currency": {"value": "USD", "confidence": 0.85},
    "gtin": {"value": "00012345678905", "confidence": 0.9},
}


def _agent(
    llm_client: FakeAgentLLMClient,
    search_provider: FakeSearchProvider,
    session: Session,
    **kwargs: Any,
) -> ProductAgent:
    return ProductAgent(llm_client=llm_client, search_provider=search_provider, session=session, **kwargs)


# --- Scenario A: duplicate check -> validate -> save -------------------------------------


def test_llm_chooses_duplicate_then_validate_then_save(session: Session) -> None:
    llm_client = FakeAgentLLMClient(
        extraction_payload=FULL_VALID_PAYLOAD,
        turns=[
            ("check_duplicate_product", {}),
            ("validate_product", {}),
            ("save_product", {}),
        ],
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/a.jpg"
    )

    assert result.outcome == AgentOutcome.SAVED
    assert result.product is not None
    assert result.product.validation_status == ValidationStatus.VALID
    assert search_provider.calls == []  # the LLM never asked for search - never called

    tool_names = [c.tool_name for c in result.trace.tool_calls]
    assert tool_names == [
        "extract_product_attributes",
        "check_duplicate_product",
        "validate_product",
        "save_product",
    ]
    assert all(c.llm_initiated for c in result.trace.tool_calls[1:])
    assert repository.find_by_id(session, result.product.id) is not None


# --- Scenario B: search first (missing info) -> validate -> duplicate -> save ------------


def test_llm_chooses_search_when_info_is_missing_then_validates_and_saves(session: Session) -> None:
    llm_client = FakeAgentLLMClient(
        extraction_payload={"title": {"value": "Widget Pro", "confidence": 0.9}},
        turns=[
            ("search_product_info", {"fields": ["category", "price", "currency"]}),
            ("validate_product", {}),
            ("check_duplicate_product", {}),
            ("save_product", {}),
        ],
    )
    search_provider = FakeSearchProvider(
        responses={
            "category": [ProductSearchResult(field="category", value="Home > Widgets", confidence=0.7)],
            "price": [ProductSearchResult(field="price", value=29.99, confidence=0.6)],
            "currency": [ProductSearchResult(field="currency", value="USD", confidence=0.6)],
        }
    )
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/b.jpg"
    )

    assert result.outcome == AgentOutcome.SAVED
    assert result.product is not None
    assert result.product.category == "Home > Widgets"
    assert result.product.price == 29.99
    assert set(search_provider.calls) == {"category", "price", "currency"}

    tool_names = [c.tool_name for c in result.trace.tool_calls]
    # search happens BEFORE duplicate check here - a different order than Scenario A,
    # chosen entirely by the scripted "LLM", not by any fixed Python sequence.
    assert tool_names == [
        "extract_product_attributes",
        "search_product_info",
        "validate_product",
        "check_duplicate_product",
        "save_product",
    ]


# --- Scenario C: validate -> human review -------------------------------------------------


def test_llm_chooses_validate_then_human_review(session: Session) -> None:
    payload = {**FULL_VALID_PAYLOAD, "gtin": {"value": "00012345678900", "confidence": 0.9}}  # bad checksum
    llm_client = FakeAgentLLMClient(
        extraction_payload=payload,
        turns=[
            ("validate_product", {}),
            ("flag_for_human_review", {"reason": "GTIN failed checksum validation"}),
        ],
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/c.jpg"
    )

    assert result.outcome == AgentOutcome.NEEDS_REVIEW
    assert result.product is not None
    assert result.product.validation_status == ValidationStatus.NEEDS_REVIEW
    assert result.validation is not None
    assert any("checksum" in e for e in result.validation.errors)
    assert search_provider.calls == []


# --- Safety: repeated ineffective tool call ------------------------------------------------


def test_repeated_identical_tool_call_is_aborted_safely(session: Session) -> None:
    llm_client = FakeAgentLLMClient(
        extraction_payload={"title": {"value": "Widget", "confidence": 0.9}},
        turns=[
            ("validate_product", {}),
            ("validate_product", {}),
            ("validate_product", {}),  # 3rd identical call in a row -> should trigger the guard
        ],
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/repeat.jpg"
    )

    assert result.outcome == AgentOutcome.NEEDS_REVIEW
    assert "repeatedly" in result.reason.lower()

    tool_names = [c.tool_name for c in result.trace.tool_calls]
    assert tool_names == [
        "extract_product_attributes",
        "validate_product",
        "validate_product",
        "validate_product",
        "flag_for_human_review",
    ]
    assert tool_names.count("validate_product") == 3
    assert result.trace.tool_calls[3].error is not None  # the 3rd call is the one that got aborted
    assert result.trace.tool_calls[4].llm_initiated is False  # forced by the orchestrator, not the LLM


# --- Safety: iteration limit ---------------------------------------------------------------


def test_iteration_limit_forces_review_before_completing(session: Session) -> None:
    llm_client = FakeAgentLLMClient(
        extraction_payload={"title": {"value": "Widget", "confidence": 0.9}},
        turns=[
            ("check_duplicate_product", {}),
            ("validate_product", {}),
            ("save_product", {}),
        ],
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session, max_iterations=2)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/limit.jpg"
    )

    assert result.outcome == AgentOutcome.NEEDS_REVIEW
    assert "iteration limit" in result.reason.lower()
    assert result.iterations_used == 3

    tool_names = [c.tool_name for c in result.trace.tool_calls]
    # only one real LLM decision fit inside the budget before the cap forced a stop
    assert tool_names == ["extract_product_attributes", "check_duplicate_product", "flag_for_human_review"]


# --- Tool execution failure ------------------------------------------------------------------


def test_tool_execution_failure_is_handled_gracefully(session: Session) -> None:
    llm_client = FakeAgentLLMClient(
        extraction_payload={"title": {"value": "Widget", "confidence": 0.9}},
        turns=[
            ("search_product_info", {"fields": ["category"]}),
            ("flag_for_human_review", {"reason": "search backend failed"}),
        ],
    )
    search_provider = FakeSearchProvider(error=LLMClientError("search backend unavailable"))
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/toolfail.jpg"
    )

    assert result.outcome == AgentOutcome.NEEDS_REVIEW
    search_call = next(c for c in result.trace.tool_calls if c.tool_name == "search_product_info")
    assert search_call.error is not None


def test_agent_decision_api_failure_results_in_controlled_outcome(session: Session) -> None:
    llm_client = FakeAgentLLMClient(
        extraction_payload=FULL_VALID_PAYLOAD,
        turns=[("check_duplicate_product", {})],
        error_at_turn=1,  # fails on the *second* send_agent_turn call
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/apifail.jpg"
    )

    assert result.outcome == AgentOutcome.AGENT_ERROR
    assert "failed" in result.reason.lower()
    assert result.product is None


# --- Extraction failure ---------------------------------------------------------------------


def test_extraction_failure_terminates_safely_without_touching_the_database(session: Session) -> None:
    llm_client = FakeAgentLLMClient(extraction_error=LLMClientError("vision model unavailable"))
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/fail.jpg"
    )

    assert result.outcome == AgentOutcome.EXTRACTION_FAILED
    assert result.product is None
    tool_names = [c.tool_name for c in result.trace.tool_calls]
    assert tool_names == ["extract_product_attributes"]


# --- Safety gate: duplicate product cannot be saved ------------------------------------------


def test_duplicate_product_cannot_be_saved(session: Session, sample_product: Product) -> None:
    sample_product = sample_product.model_copy(
        update={"validation_status": ValidationStatus.VALID, "validation_errors": []}
    )
    repository.save_product(session, sample_product)

    payload = {
        "title": {"value": sample_product.title, "confidence": 0.9},
        "category": {"value": sample_product.category, "confidence": 0.9},
        "price": {"value": sample_product.price, "confidence": 0.9},
        "currency": {"value": sample_product.currency, "confidence": 0.9},
        "gtin": {"value": sample_product.gtin, "confidence": 0.95},
    }
    llm_client = FakeAgentLLMClient(
        extraction_payload=payload,
        turns=[
            ("check_duplicate_product", {}),
            ("validate_product", {}),
            ("save_product", {}),  # should be blocked - this IS a duplicate
            ("flag_for_human_review", {"reason": "duplicate of an existing product"}),
        ],
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/dup.jpg"
    )

    assert result.outcome == AgentOutcome.DUPLICATE

    save_attempt = next(c for c in result.trace.tool_calls if c.tool_name == "save_product")
    assert save_attempt.error is not None
    assert "duplicate" in save_attempt.error.lower()

    # the original product is untouched and still the only VALID record
    original = repository.find_by_id(session, sample_product.id)
    assert original is not None
    assert original.validation_status == ValidationStatus.VALID

    # the agent's own draft was persisted separately, marked for review - not as a second
    # valid copy of the same product
    assert result.product is not None
    assert result.product.id != sample_product.id
    assert result.product.validation_status == ValidationStatus.NEEDS_REVIEW


# --- Safety gate: invalid product cannot be saved ---------------------------------------------


def test_invalid_product_cannot_be_saved(session: Session) -> None:
    payload = {
        "title": {"value": "Widget", "confidence": 0.9},
        # category deliberately missing -> validate_product will report a blocking error
        "price": {"value": 10.0, "confidence": 0.9},
        "currency": {"value": "USD", "confidence": 0.9},
    }
    llm_client = FakeAgentLLMClient(
        extraction_payload=payload,
        turns=[
            ("validate_product", {}),
            ("save_product", {}),  # should be blocked - validation failed
            ("flag_for_human_review", {"reason": "missing category"}),
        ],
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/invalid.jpg"
    )

    assert result.outcome == AgentOutcome.NEEDS_REVIEW
    save_attempt = next(c for c in result.trace.tool_calls if c.tool_name == "save_product")
    assert save_attempt.error is not None
    assert "blocking errors" in save_attempt.error.lower()


def test_save_without_any_prior_checks_is_blocked(session: Session) -> None:
    llm_client = FakeAgentLLMClient(
        extraction_payload=FULL_VALID_PAYLOAD,
        turns=[
            ("save_product", {}),  # no validate_product or check_duplicate_product call at all
            ("validate_product", {}),
            ("check_duplicate_product", {}),
            ("save_product", {}),
        ],
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/noprereq.jpg"
    )

    assert result.outcome == AgentOutcome.SAVED
    save_calls = [c for c in result.trace.tool_calls if c.tool_name == "save_product"]
    assert len(save_calls) == 2
    assert save_calls[0].error is not None
    assert "has not been called" in save_calls[0].error
    assert save_calls[1].error is None


def test_agent_recovers_from_a_turn_without_a_tool_call(session: Session) -> None:
    llm_client = FakeAgentLLMClient(
        extraction_payload=FULL_VALID_PAYLOAD,
        turns=[
            None,  # the model just talks instead of calling a tool
            ("check_duplicate_product", {}),
            ("validate_product", {}),
            ("save_product", {}),
        ],
    )
    search_provider = FakeSearchProvider()
    agent = _agent(llm_client, search_provider, session)

    result = agent.run(
        image_bytes=b"img", image_media_type="image/jpeg", source_image_url="https://example.com/notool.jpg"
    )

    assert result.outcome == AgentOutcome.SAVED
    tool_names = [c.tool_name for c in result.trace.tool_calls]
    assert "agent_turn_no_tool_call" in tool_names
