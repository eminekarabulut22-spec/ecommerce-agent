import base64
from typing import Any

import pytest

from ecommerce_agent.llm.client import ImageMediaType, LLMClientError
from ecommerce_agent.models.product import FieldSource
from ecommerce_agent.tools.extraction import ProductExtractionError, extract_product_attributes


class FakeLLMClient:
    """Test double for LLMClient: returns a canned payload or raises a canned error."""

    def __init__(
        self,
        *,
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._payload = payload
        self._error = error
        self.last_call_kwargs: dict[str, Any] | None = None

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
        self.last_call_kwargs = {
            "system_prompt": system_prompt,
            "user_text": user_text,
            "image_base64": image_base64,
            "image_media_type": image_media_type,
            "tool_name": tool_name,
            "tool_description": tool_description,
            "tool_schema": tool_schema,
        }
        if self._error is not None:
            raise self._error
        assert self._payload is not None
        return self._payload


FULL_PAYLOAD = {
    "title": {"value": "Wireless Mechanical Keyboard", "confidence": 0.95},
    "description": {"value": "A compact 65% mechanical keyboard.", "confidence": 0.8},
    "category": {"value": "Electronics > Keyboards", "confidence": 0.85},
    "brand": {"value": "Keychron", "confidence": 0.4},
    "color": {"value": "black", "confidence": 0.9},
    "price": {"value": 89.99, "confidence": 0.6},
    "currency": {"value": "USD", "confidence": 0.6},
    "tags": ["keyboard", "mechanical", "wireless"],
}


def test_extract_product_attributes_success() -> None:
    llm_client = FakeLLMClient(payload=FULL_PAYLOAD)

    draft = extract_product_attributes(
        image_bytes=b"fake-image-bytes",
        image_media_type="image/jpeg",
        source_image_url="https://example.com/keyboard.jpg",
        llm_client=llm_client,
    )

    assert draft.title is not None
    assert draft.title.value == "Wireless Mechanical Keyboard"
    assert draft.title.confidence == 0.95
    assert draft.title.source == FieldSource.VISION_EXTRACTION

    assert draft.brand is not None
    assert draft.brand.confidence == 0.4

    assert draft.price is not None
    assert draft.price.value == 89.99

    assert draft.manufacturer is None
    assert draft.gtin is None

    assert draft.tags == ["keyboard", "mechanical", "wireless"]
    assert draft.raw_llm_responses == [FULL_PAYLOAD]
    assert draft.source_image_url == "https://example.com/keyboard.jpg"


def test_extract_product_attributes_encodes_image_and_forwards_call_details() -> None:
    llm_client = FakeLLMClient(payload={"title": {"value": "X", "confidence": 0.5}})
    raw_bytes = b"\x89PNGnotarealpng"

    extract_product_attributes(
        image_bytes=raw_bytes,
        image_media_type="image/png",
        source_image_url="https://example.com/x.png",
        llm_client=llm_client,
    )

    assert llm_client.last_call_kwargs is not None
    sent_b64 = llm_client.last_call_kwargs["image_base64"]
    assert base64.standard_b64decode(sent_b64) == raw_bytes
    assert llm_client.last_call_kwargs["image_media_type"] == "image/png"
    assert llm_client.last_call_kwargs["tool_name"] == "record_product_draft"


def test_extract_product_attributes_minimal_payload_leaves_optional_fields_none() -> None:
    llm_client = FakeLLMClient(payload={"title": {"value": "Mystery Item", "confidence": 0.5}})

    draft = extract_product_attributes(
        image_bytes=b"bytes",
        image_media_type="image/jpeg",
        source_image_url="https://example.com/x.jpg",
        llm_client=llm_client,
    )

    assert draft.title is not None
    assert draft.title.value == "Mystery Item"
    assert draft.description is None
    assert draft.price is None
    assert draft.tags == []
    assert set(draft.missing_fields()) == {"description", "category", "price"}


def test_extract_product_attributes_raises_when_title_missing() -> None:
    llm_client = FakeLLMClient(payload={"color": {"value": "black", "confidence": 0.9}})

    with pytest.raises(ProductExtractionError):
        extract_product_attributes(
            image_bytes=b"bytes",
            image_media_type="image/jpeg",
            source_image_url="https://example.com/x.jpg",
            llm_client=llm_client,
        )


def test_extract_product_attributes_raises_on_out_of_range_confidence() -> None:
    llm_client = FakeLLMClient(
        payload={"title": {"value": "Keyboard", "confidence": 1.5}}
    )

    with pytest.raises(ProductExtractionError):
        extract_product_attributes(
            image_bytes=b"bytes",
            image_media_type="image/jpeg",
            source_image_url="https://example.com/x.jpg",
            llm_client=llm_client,
        )


def test_extract_product_attributes_raises_on_wrong_value_type() -> None:
    llm_client = FakeLLMClient(
        payload={
            "title": {"value": "Keyboard", "confidence": 0.9},
            "price": {"value": "expensive", "confidence": 0.5},
        }
    )

    with pytest.raises(ProductExtractionError):
        extract_product_attributes(
            image_bytes=b"bytes",
            image_media_type="image/jpeg",
            source_image_url="https://example.com/x.jpg",
            llm_client=llm_client,
        )


def test_extract_product_attributes_coerces_tags_wrapped_as_json_string_object() -> None:
    """Regression test: a real API response returned `tags` as the JSON string
    '{"value": ["freediving fins", ...]}' instead of a native list, because the
    model wrapped it like the other `{value, confidence}` fields and then
    serialized that object as a string. This must be coerced back into a plain
    list of strings rather than failing validation."""
    llm_client = FakeLLMClient(
        payload={
            "title": {"value": "Freediving Fins", "confidence": 0.9},
            "tags": '{"value": ["freediving fins", "scuba", "watersports"]}',
        }
    )

    draft = extract_product_attributes(
        image_bytes=b"bytes",
        image_media_type="image/jpeg",
        source_image_url="https://example.com/fins.jpg",
        llm_client=llm_client,
    )

    assert draft.tags == ["freediving fins", "scuba", "watersports"]


def test_extract_product_attributes_coerces_tags_as_json_string_array() -> None:
    llm_client = FakeLLMClient(
        payload={
            "title": {"value": "Item", "confidence": 0.9},
            "tags": '["a", "b", "c"]',
        }
    )

    draft = extract_product_attributes(
        image_bytes=b"bytes",
        image_media_type="image/jpeg",
        source_image_url="https://example.com/item.jpg",
        llm_client=llm_client,
    )

    assert draft.tags == ["a", "b", "c"]


def test_extract_product_attributes_coerces_tags_as_value_wrapped_object() -> None:
    llm_client = FakeLLMClient(
        payload={
            "title": {"value": "Item", "confidence": 0.9},
            "tags": {"value": ["a", "b"]},
        }
    )

    draft = extract_product_attributes(
        image_bytes=b"bytes",
        image_media_type="image/jpeg",
        source_image_url="https://example.com/item.jpg",
        llm_client=llm_client,
    )

    assert draft.tags == ["a", "b"]


def test_extract_product_attributes_raises_on_unparsable_tags_string() -> None:
    llm_client = FakeLLMClient(
        payload={
            "title": {"value": "Item", "confidence": 0.9},
            "tags": "not json",
        }
    )

    with pytest.raises(ProductExtractionError):
        extract_product_attributes(
            image_bytes=b"bytes",
            image_media_type="image/jpeg",
            source_image_url="https://example.com/item.jpg",
            llm_client=llm_client,
        )


def test_extract_product_attributes_raises_on_tags_object_without_value_key() -> None:
    llm_client = FakeLLMClient(
        payload={
            "title": {"value": "Item", "confidence": 0.9},
            "tags": {"items": ["a", "b"]},
        }
    )

    with pytest.raises(ProductExtractionError):
        extract_product_attributes(
            image_bytes=b"bytes",
            image_media_type="image/jpeg",
            source_image_url="https://example.com/item.jpg",
            llm_client=llm_client,
        )


def test_extract_product_attributes_raises_on_tags_list_with_non_string_items() -> None:
    llm_client = FakeLLMClient(
        payload={
            "title": {"value": "Item", "confidence": 0.9},
            "tags": [1, 2, 3],
        }
    )

    with pytest.raises(ProductExtractionError):
        extract_product_attributes(
            image_bytes=b"bytes",
            image_media_type="image/jpeg",
            source_image_url="https://example.com/item.jpg",
            llm_client=llm_client,
        )


def test_extract_product_attributes_propagates_llm_client_errors() -> None:
    llm_client = FakeLLMClient(error=LLMClientError("model timed out"))

    with pytest.raises(LLMClientError):
        extract_product_attributes(
            image_bytes=b"bytes",
            image_media_type="image/jpeg",
            source_image_url="https://example.com/x.jpg",
            llm_client=llm_client,
        )
