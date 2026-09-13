import pytest
from pydantic import ValidationError

from ecommerce_agent.models.product import (
    FieldConfidence,
    FieldSource,
    Product,
    ProductDraft,
    ValidationStatus,
)


def test_product_requires_non_empty_title(sample_product: Product) -> None:
    with pytest.raises(ValidationError):
        Product(**{**sample_product.model_dump(), "title": ""})


def test_product_rejects_negative_price(sample_product: Product) -> None:
    with pytest.raises(ValidationError):
        Product(**{**sample_product.model_dump(), "price": -1.0})


def test_product_defaults_to_needs_review(sample_product: Product) -> None:
    assert sample_product.validation_status == ValidationStatus.NEEDS_REVIEW
    assert sample_product.validation_errors == []


def test_draft_missing_fields_reports_unpopulated_core_fields() -> None:
    draft = ProductDraft(source_image_url="https://example.com/img.jpg")
    assert set(draft.missing_fields()) == {"title", "description", "category", "price"}


def test_draft_missing_fields_shrinks_as_fields_are_populated() -> None:
    draft = ProductDraft(
        source_image_url="https://example.com/img.jpg",
        title=FieldConfidence(value="Keyboard", confidence=0.9, source=FieldSource.VISION_EXTRACTION),
    )
    assert "title" not in draft.missing_fields()
    assert "price" in draft.missing_fields()


def test_draft_low_confidence_fields_uses_threshold() -> None:
    draft = ProductDraft(
        source_image_url="https://example.com/img.jpg",
        title=FieldConfidence(value="Keyboard", confidence=0.9, source=FieldSource.VISION_EXTRACTION),
        brand=FieldConfidence(value="Maybe Keychron", confidence=0.3, source=FieldSource.VISION_EXTRACTION),
    )
    assert draft.low_confidence_fields(threshold=0.5) == ["brand"]
    assert draft.low_confidence_fields(threshold=0.2) == []


def test_product_from_draft_flattens_values_and_confidence() -> None:
    draft = ProductDraft(
        source_image_url="https://example.com/img.jpg",
        title=FieldConfidence(value="Keyboard", confidence=0.9, source=FieldSource.VISION_EXTRACTION),
        price=FieldConfidence(value=49.99, confidence=0.6, source=FieldSource.WEB_SEARCH),
        color=FieldConfidence(value="black", confidence=0.8, source=FieldSource.VISION_EXTRACTION),
    )

    product = Product.from_draft(draft, extraction_model="claude-sonnet-5")

    assert product.id == draft.id
    assert product.title == "Keyboard"
    assert product.price == 49.99
    assert product.attributes["color"] == "black"
    assert product.confidence_scores["title"] == 0.9
    assert product.confidence_scores["price"] == 0.6
    assert product.extraction_model == "claude-sonnet-5"


def test_product_from_draft_falls_back_when_title_missing() -> None:
    draft = ProductDraft(source_image_url="https://example.com/img.jpg")
    product = Product.from_draft(draft)
    assert product.title == "Untitled product"
