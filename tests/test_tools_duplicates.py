from uuid import uuid4

from sqlalchemy.orm import Session

from ecommerce_agent.db import repository
from ecommerce_agent.models.product import FieldConfidence, FieldSource, Product, ProductDraft
from ecommerce_agent.tools.duplicates import check_duplicate_product


def test_no_duplicate_when_gtin_not_in_database(session: Session, sample_product: Product) -> None:
    result = check_duplicate_product(session, sample_product)
    assert result.is_duplicate is False
    assert result.matched_product_id is None
    assert result.existing_product is None


def test_duplicate_found_by_gtin_for_product_input(session: Session, sample_product: Product) -> None:
    saved = repository.save_product(session, sample_product)

    incoming = sample_product.model_copy(update={"id": uuid4()})
    result = check_duplicate_product(session, incoming)

    assert result.is_duplicate is True
    assert result.matched_by == "gtin"
    assert result.matched_product_id == saved.id
    assert result.existing_product is not None
    assert result.existing_product.title == sample_product.title


def test_duplicate_found_by_gtin_for_draft_input(session: Session, sample_product: Product) -> None:
    saved = repository.save_product(session, sample_product)

    draft = ProductDraft(
        source_image_url="https://example.com/other.jpg",
        gtin=FieldConfidence(
            value=sample_product.gtin, confidence=0.9, source=FieldSource.VISION_EXTRACTION
        ),
    )
    result = check_duplicate_product(session, draft)

    assert result.is_duplicate is True
    assert result.matched_product_id == saved.id


def test_no_duplicate_when_gtin_missing_on_draft(session: Session) -> None:
    draft = ProductDraft(source_image_url="https://example.com/img.jpg")
    result = check_duplicate_product(session, draft)
    assert result.is_duplicate is False


def test_no_duplicate_when_gtin_differs(session: Session, sample_product: Product) -> None:
    repository.save_product(session, sample_product)

    other = sample_product.model_copy(update={"id": uuid4(), "gtin": "99999999999999"})
    result = check_duplicate_product(session, other)

    assert result.is_duplicate is False
