from typing import Any

import pytest

from ecommerce_agent.config import get_settings
from ecommerce_agent.llm import client as client_module
from ecommerce_agent.llm.client import AnthropicLLMClient, LLMClientError


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


def _patch_anthropic(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> _FakeMessagesAPI:
    holder: dict[str, _FakeAnthropicSDKClient] = {}

    def factory(*, api_key: str) -> _FakeAnthropicSDKClient:
        fake = _FakeAnthropicSDKClient(response, api_key=api_key)
        holder["client"] = fake
        return fake

    monkeypatch.setattr(client_module.anthropic, "Anthropic", factory)
    # Return the messages API lazily via the holder once the client has been constructed.
    return holder


def test_missing_api_key_raises_without_calling_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    # `Settings` loads `.env` directly, so a real key there would still be picked up
    # by `delenv` alone (dotenv values are only overridden by real env vars, not by
    # their absence). Set the env var to "" to reliably simulate a missing key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    with pytest.raises(LLMClientError):
        AnthropicLLMClient(api_key=None)


def test_call_structured_tool_returns_tool_input(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_input = {"title": {"value": "Keyboard", "confidence": 0.9}}
    response = _FakeResponse(content=[_FakeToolUseBlock("record_product_draft", expected_input)])
    holder = _patch_anthropic(monkeypatch, response)

    llm_client = AnthropicLLMClient(api_key="test-key", model="claude-sonnet-5")
    result = llm_client.call_structured_tool(
        system_prompt="system",
        user_text="describe this",
        image_base64="ZmFrZS1pbWFnZQ==",
        image_media_type="image/jpeg",
        tool_name="record_product_draft",
        tool_description="record it",
        tool_schema={"type": "object", "properties": {}},
    )

    assert result == expected_input

    captured = holder["client"].messages.captured_kwargs
    assert captured is not None
    assert captured["model"] == "claude-sonnet-5"
    assert captured["tool_choice"] == {"type": "tool", "name": "record_product_draft"}
    assert captured["tools"][0]["name"] == "record_product_draft"
    image_block = captured["messages"][0]["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["media_type"] == "image/jpeg"
    assert image_block["source"]["data"] == "ZmFrZS1pbWFnZQ=="


def test_call_structured_tool_ignores_non_tool_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    expected_input = {"title": {"value": "Keyboard", "confidence": 0.9}}
    response = _FakeResponse(
        content=[
            _FakeTextBlock("thinking out loud"),
            _FakeToolUseBlock("record_product_draft", expected_input),
        ]
    )
    _patch_anthropic(monkeypatch, response)

    llm_client = AnthropicLLMClient(api_key="test-key")
    result = llm_client.call_structured_tool(
        system_prompt="system",
        user_text="describe this",
        image_base64="ZmFrZQ==",
        image_media_type="image/png",
        tool_name="record_product_draft",
        tool_description="record it",
        tool_schema={"type": "object", "properties": {}},
    )

    assert result == expected_input


def test_call_structured_tool_raises_when_tool_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(content=[_FakeTextBlock("I refuse to use tools today.")])
    _patch_anthropic(monkeypatch, response)

    llm_client = AnthropicLLMClient(api_key="test-key")

    with pytest.raises(LLMClientError):
        llm_client.call_structured_tool(
            system_prompt="system",
            user_text="describe this",
            image_base64="ZmFrZQ==",
            image_media_type="image/png",
            tool_name="record_product_draft",
            tool_description="record it",
            tool_schema={"type": "object", "properties": {}},
        )
