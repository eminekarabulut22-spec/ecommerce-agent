from typing import Any, Literal, Protocol

import anthropic
from pydantic import BaseModel, Field, ValidationError

from ecommerce_agent.config import get_settings
from ecommerce_agent.llm.prompts import (
    PRODUCT_SEARCH_REPORT_TOOL_DESCRIPTION,
    PRODUCT_SEARCH_REPORT_TOOL_NAME,
    PRODUCT_SEARCH_REPORT_TOOL_SCHEMA,
    PRODUCT_SEARCH_SYSTEM_PROMPT,
)

ImageMediaType = Literal["image/jpeg", "image/png", "image/webp", "image/gif"]

# Dynamic-filtering web search tool (current-generation models); see the claude-api skill's
# Server Tools reference. Older models would need the basic "web_search_20250305" variant.
_WEB_SEARCH_TOOL: dict[str, str] = {"type": "web_search_20260209", "name": "web_search"}


class LLMClientError(Exception):
    """Raised when an LLM call fails outright or returns something we cannot use."""


class LLMClient(Protocol):
    """Abstraction over a multimodal, tool-using LLM call.

    Anything that needs to call the model (extraction tools today, the agent orchestrator
    later) should depend on this Protocol rather than on `AnthropicLLMClient` or the
    `anthropic` package directly, so a different provider or a test double can be swapped
    in without touching calling code.
    """

    def call_structured_tool(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_base64: str,
        image_media_type: ImageMediaType,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Send an image + instructions, forcing the model to answer via the given tool.

        Returns the tool call's `input` payload. Raises `LLMClientError` if the call fails
        or the model's response does not include a call to the requested tool.
        """
        ...

    def send_agent_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> "AgentTurnResult":
        """Send the running conversation with `tool_choice: auto` and return the model's turn.

        Unlike `call_structured_tool` (one forced tool, one-shot), this lets the model choose
        freely among several tools - or none - each turn, for an open-ended decision loop the
        caller drives by appending tool results and calling this again.
        """
        ...


class AnthropicLLMClient:
    """`LLMClient` implementation backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        settings = get_settings()
        resolved_key = api_key or settings.anthropic_api_key
        if not resolved_key:
            raise LLMClientError(
                "No Anthropic API key configured. Set ANTHROPIC_API_KEY in the environment/.env."
            )
        self._client = anthropic.Anthropic(api_key=resolved_key)
        self._model = model or settings.anthropic_model
        self._max_tokens = max_tokens

    def call_structured_tool(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_base64: str,
        image_media_type: ImageMediaType,
        tool_name: str,
        tool_description: str,
        tool_schema: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                tools=[
                    {
                        "name": tool_name,
                        "description": tool_description,
                        "input_schema": tool_schema,
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image_media_type,
                                    "data": image_base64,
                                },
                            },
                            {"type": "text", "text": user_text},
                        ],
                    }
                ],
            )
        except anthropic.APIError as exc:
            raise LLMClientError(f"Anthropic API call failed: {exc}") from exc

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                return block.input

        raise LLMClientError(f"Model response did not include a '{tool_name}' tool call.")

    def send_agent_turn(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> "AgentTurnResult":
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_prompt,
                tools=tools,
                tool_choice={"type": "auto"},
                messages=messages,
            )
        except anthropic.APIError as exc:
            raise LLMClientError(f"Anthropic API call failed: {exc}") from exc

        assistant_content = response.to_dict()["content"]

        tool_use: AgentToolUse | None = None
        text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "tool_use" and tool_use is None:
                tool_use = AgentToolUse(id=block.id, name=block.name, input=block.input)
            elif block_type == "text":
                text_parts.append(block.text)

        return AgentTurnResult(
            stop_reason=response.stop_reason,
            assistant_content=assistant_content,
            tool_use=tool_use,
            text="\n".join(text_parts) if text_parts else None,
        )


class AgentToolUse(BaseModel):
    """A single tool call the model requested, in a vendor-neutral shape."""

    id: str
    name: str
    input: dict[str, Any]


class AgentTurnResult(BaseModel):
    """One assistant turn in a multi-tool, auto-tool-choice conversation.

    `assistant_content` is the raw response content as plain dicts (never `anthropic` SDK
    types) - the caller must append it verbatim as the next assistant message to keep the
    conversation coherent for the next turn.
    """

    stop_reason: str
    assistant_content: list[dict[str, Any]]
    tool_use: AgentToolUse | None = None
    text: str | None = None


class ProductSearchResult(BaseModel):
    """A single web-search finding for one requested product field."""

    field: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    source_url: str | None = None


class SearchProvider(Protocol):
    """Abstraction over "find more information about this product attribute."

    `search_product_info` (tools/search.py) depends on this Protocol rather than on any
    specific search vendor, so the provider - a different search API, or a test double -
    can be swapped in without touching the tool logic.
    """

    def search_product_field(
        self,
        *,
        product_title: str,
        field: str,
        context: dict[str, Any] | None = None,
    ) -> list[ProductSearchResult]:
        """Search for the given field. Returns an empty list if nothing reliable was found."""
        ...


class AnthropicWebSearchProvider:
    """`SearchProvider` implementation backed by Claude's server-side web search tool."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        settings = get_settings()
        resolved_key = api_key or settings.anthropic_api_key
        if not resolved_key:
            raise LLMClientError(
                "No Anthropic API key configured. Set ANTHROPIC_API_KEY in the environment/.env."
            )
        self._client = anthropic.Anthropic(api_key=resolved_key)
        self._model = model or settings.anthropic_model
        self._max_tokens = max_tokens

    def search_product_field(
        self,
        *,
        product_title: str,
        field: str,
        context: dict[str, Any] | None = None,
    ) -> list[ProductSearchResult]:
        context_lines = "\n".join(f"- {k}: {v}" for k, v in (context or {}).items())
        user_text = (
            f'Find the "{field}" for this product: "{product_title}".\n'
            + (f"Known details:\n{context_lines}\n" if context_lines else "")
            + f"Search the web, then report your single best answer for '{field}' using the "
            f"{PRODUCT_SEARCH_REPORT_TOOL_NAME} tool. If you cannot find a reliable answer, do "
            "not call the tool."
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=PRODUCT_SEARCH_SYSTEM_PROMPT,
                tools=[
                    _WEB_SEARCH_TOOL,
                    {
                        "name": PRODUCT_SEARCH_REPORT_TOOL_NAME,
                        "description": PRODUCT_SEARCH_REPORT_TOOL_DESCRIPTION,
                        "input_schema": PRODUCT_SEARCH_REPORT_TOOL_SCHEMA,
                    },
                ],
                tool_choice={"type": "auto"},
                messages=[{"role": "user", "content": user_text}],
            )
        except anthropic.APIError as exc:
            raise LLMClientError(f"Anthropic web search call failed: {exc}") from exc

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == PRODUCT_SEARCH_REPORT_TOOL_NAME:
                try:
                    return [ProductSearchResult(field=field, **block.input)]
                except ValidationError as exc:
                    raise LLMClientError(
                        f"Model returned an invalid search result payload: {exc}"
                    ) from exc

        return []
