import base64
import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from ecommerce_agent.llm.client import ImageMediaType, LLMClient
from ecommerce_agent.llm.prompts import (
    PRODUCT_EXTRACTION_SYSTEM_PROMPT,
    PRODUCT_EXTRACTION_TOOL_DESCRIPTION,
    PRODUCT_EXTRACTION_TOOL_NAME,
    PRODUCT_EXTRACTION_TOOL_SCHEMA,
)
from ecommerce_agent.models.product import FieldConfidence, FieldSource, ProductDraft


class ProductExtractionError(Exception):
    """Raised when the model's tool-call payload does not match the expected extraction shape."""


class _ExtractedStringField(BaseModel):
    value: str
    confidence: float = Field(ge=0.0, le=1.0)


class _ExtractedPriceField(BaseModel):
    value: float = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)


class _ExtractedPayload(BaseModel):
    """Validates the raw tool-call input. Keep in sync with PRODUCT_EXTRACTION_TOOL_SCHEMA."""

    title: _ExtractedStringField
    description: _ExtractedStringField | None = None
    category: _ExtractedStringField | None = None
    brand: _ExtractedStringField | None = None
    manufacturer: _ExtractedStringField | None = None
    color: _ExtractedStringField | None = None
    material: _ExtractedStringField | None = None
    dimensions: _ExtractedStringField | None = None
    weight: _ExtractedStringField | None = None
    price: _ExtractedPriceField | None = None
    currency: _ExtractedStringField | None = None
    gtin: _ExtractedStringField | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: Any) -> Any:
        """Normalize alternate `tags` shapes the model sometimes returns.

        Despite the tool schema declaring `tags` as a plain string array, the model
        has been observed (a) wrapping it like the other fields, as
        `{"value": [...]}`, and/or (b) serializing that object (or the array itself)
        as a JSON string rather than a native array. Unwrap both cases here so the
        standard `list[str]` validation below still enforces the real shape.
        """
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"tags string is not valid JSON: {value!r}"
                ) from exc
        if isinstance(value, dict):
            if "value" not in value:
                raise ValueError(
                    f"tags object must contain a 'value' key, got keys: {list(value.keys())}"
                )
            value = value["value"]
        return value


def _to_field_confidence(
    extracted: _ExtractedStringField | _ExtractedPriceField | None,
) -> FieldConfidence | None:
    if extracted is None:
        return None
    return FieldConfidence(
        value=extracted.value,
        confidence=extracted.confidence,
        source=FieldSource.VISION_EXTRACTION,
    )


def _payload_to_draft(
    payload: _ExtractedPayload,
    *,
    source_image_url: str,
    raw_response: dict[str, Any],
) -> ProductDraft:
    return ProductDraft(
        source_image_url=source_image_url,
        title=_to_field_confidence(payload.title),
        description=_to_field_confidence(payload.description),
        category=_to_field_confidence(payload.category),
        brand=_to_field_confidence(payload.brand),
        manufacturer=_to_field_confidence(payload.manufacturer),
        color=_to_field_confidence(payload.color),
        material=_to_field_confidence(payload.material),
        dimensions=_to_field_confidence(payload.dimensions),
        weight=_to_field_confidence(payload.weight),
        price=_to_field_confidence(payload.price),
        currency=_to_field_confidence(payload.currency),
        gtin=_to_field_confidence(payload.gtin),
        tags=payload.tags,
        raw_llm_responses=[raw_response],
    )


def extract_product_attributes(
    *,
    image_bytes: bytes,
    image_media_type: ImageMediaType,
    source_image_url: str,
    llm_client: LLMClient,
) -> ProductDraft:
    """Analyze a product image with a multimodal LLM and return a structured `ProductDraft`.

    Raises `LLMClientError` (from the client layer) if the model call itself fails, and
    `ProductExtractionError` if the model responds but its payload doesn't match the
    expected extraction shape.
    """
    image_base64 = base64.standard_b64encode(image_bytes).decode("ascii")

    raw_payload = llm_client.call_structured_tool(
        system_prompt=PRODUCT_EXTRACTION_SYSTEM_PROMPT,
        user_text="Analyze this product image and record its attributes.",
        image_base64=image_base64,
        image_media_type=image_media_type,
        tool_name=PRODUCT_EXTRACTION_TOOL_NAME,
        tool_description=PRODUCT_EXTRACTION_TOOL_DESCRIPTION,
        tool_schema=PRODUCT_EXTRACTION_TOOL_SCHEMA,
    )

    try:
        payload = _ExtractedPayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise ProductExtractionError(
            f"Model returned an invalid extraction payload: {exc}"
        ) from exc

    return _payload_to_draft(payload, source_image_url=source_image_url, raw_response=raw_payload)
