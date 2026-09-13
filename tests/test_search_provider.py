from typing import Any

import pytest

from ecommerce_agent.config import get_settings
from ecommerce_agent.llm import client as client_module
from ecommerce_agent.llm.client import AnthropicWebSearchProvider, LLMClientError


class _FakeToolUseBlock:
    def __init__(self, name: str, input_: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessagesAPI:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.captured_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.captured_kwargs = kwargs
        return self._response


class _FakeAnthropicSDKClient:
    def __init__(self, response: _FakeResponse, *, api_key: str) -> None:
        self.api_key = api_key
        self.messages = _FakeMessagesAPI(response)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patch_anthropic(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> dict[str, Any]:
    holder: dict[str, Any] = {}

    def factory(*, api_key: str) -> _FakeAnthropicSDKClient:
        fake = _FakeAnthropicSDKClient(response, api_key=api_key)
        holder["client"] = fake
        return fake

    monkeypatch.setattr(client_module.anthropic, "Anthropic", factory)
    return holder


def test_search_product_field_returns_parsed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_input = {"value": "Keychron", "confidence": 0.85, "source_url": "https://keychron.com"}
    response = _FakeResponse(content=[_FakeToolUseBlock("report_search_finding", tool_input)])
    holder = _patch_anthropic(monkeypatch, response)

    provider = AnthropicWebSearchProvider(api_key="test-key")
    results = provider.search_product_field(product_title="Keyboard X1", field="brand")

    assert len(results) == 1
    result = results[0]
    assert result.field == "brand"
    assert result.value == "Keychron"
    assert result.confidence == 0.85
    assert result.source_url == "https://keychron.com"

    captured = holder["client"].messages.captured_kwargs
    assert captured["tool_choice"] == {"type": "auto"}
    tool_names = {t["name"] for t in captured["tools"] if "name" in t}
    assert "web_search" in tool_names
    assert "report_search_finding" in tool_names


def test_search_product_field_returns_empty_when_tool_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _FakeResponse(content=[_FakeTextBlock("I couldn't find a reliable answer.")])
    _patch_anthropic(monkeypatch, response)

    provider = AnthropicWebSearchProvider(api_key="test-key")
    results = provider.search_product_field(product_title="Keyboard X1", field="gtin")

    assert results == []


def test_search_product_field_raises_on_malformed_tool_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_input = {"value": "Keychron", "confidence": 2.5}  # confidence out of [0, 1]
    response = _FakeResponse(content=[_FakeToolUseBlock("report_search_finding", bad_input)])
    _patch_anthropic(monkeypatch, response)

    provider = AnthropicWebSearchProvider(api_key="test-key")

    with pytest.raises(LLMClientError):
        provider.search_product_field(product_title="Keyboard X1", field="brand")


def test_missing_api_key_raises_without_calling_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    # `Settings` loads `.env` directly, so a real key there would still be picked up
    # by `delenv` alone (dotenv values are only overridden by real env vars, not by
    # their absence). Set the env var to "" to reliably simulate a missing key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    with pytest.raises(LLMClientError):
        AnthropicWebSearchProvider(api_key=None)
