from ecommerce_agent.models.product import (
    FieldConfidence,
    FieldSource,
    Product,
    ProductDraft,
    ValidationStatus,
)
from ecommerce_agent.tools.validation import validate_product


def _fc(value, confidence: float = 0.9, source: FieldSource = FieldSource.VISION_EXTRACTION):
    return FieldConfidence(value=value, confidence=confidence, source=source)


def _full_valid_draft() -> ProductDraft:
    return ProductDraft(
        source_image_url="https://example.com/img.jpg",
        title=_fc("Wireless Mechanical Keyboard"),
        category=_fc("Electronics > Keyboards"),
        price=_fc(89.99),
        currency=_fc("USD"),
        gtin=_fc("00012345678905"),
    )


def test_draft_full_valid_product_passes() -> None:
    result = validate_product(_full_valid_draft())
    assert result.is_valid
    assert result.status == ValidationStatus.VALID
    assert result.errors == []


def test_draft_missing_title_is_an_error() -> None:
    draft = ProductDraft(
        source_image_url="https://example.com/img.jpg",
        category=_fc("Electronics"),
    )
    result = validate_product(draft)
    assert not result.is_valid
    assert any("Title" in e for e in result.errors)


def test_draft_missing_category_is_an_error() -> None:
    draft = ProductDraft(
        source_image_url="https://example.com/img.jpg",
        title=_fc("Keyboard"),
    )
    result = validate_product(draft)
    assert not result.is_valid
    assert any("Category" in e for e in result.errors)


def test_draft_price_without_currency_is_an_error() -> None:
    draft = _full_valid_draft()
    draft.currency = None
    result = validate_product(draft)
    assert not result.is_valid
    assert any("currency is missing" in e for e in result.errors)


def test_draft_non_positive_price_is_an_error() -> None:
    draft = _full_valid_draft()
    draft.price = _fc(0)
    result = validate_product(draft)
    assert not result.is_valid
    assert any("greater than 0" in e for e in result.errors)


def test_draft_malformed_currency_code_is_an_error() -> None:
    draft = _full_valid_draft()
    draft.currency = _fc("US")
    result = validate_product(draft)
    assert not result.is_valid
    assert any("ISO code" in e for e in result.errors)


def test_draft_gtin_wrong_length_is_an_error() -> None:
    draft = _full_valid_draft()
    draft.gtin = _fc("123")
    result = validate_product(draft)
    assert not result.is_valid
    assert any("8, 12, 13, or 14 digits" in e for e in result.errors)


def test_draft_gtin_bad_checksum_is_an_error() -> None:
    draft = _full_valid_draft()
    draft.gtin = _fc("00012345678900")  # same digits, wrong check digit
    result = validate_product(draft)
    assert not result.is_valid
    assert any("checksum" in e for e in result.errors)


def test_draft_missing_gtin_is_not_an_error() -> None:
    draft = _full_valid_draft()
    draft.gtin = None
    result = validate_product(draft)
    assert result.is_valid


def test_draft_low_confidence_field_is_a_warning_not_an_error() -> None:
    draft = _full_valid_draft()
    draft.brand = _fc("Maybe Keychron", confidence=0.2)
    result = validate_product(draft, confidence_threshold=0.5)
    assert result.is_valid  # warnings don't block validity
    assert any("brand" in w for w in result.warnings)


def test_product_valid_final_record_passes() -> None:
    product = Product(
        title="Wireless Mechanical Keyboard",
        category="Electronics > Keyboards",
        price=89.99,
        currency="USD",
        gtin="00012345678905",
        source_image_url="https://example.com/img.jpg",
        confidence_scores={"title": 0.95, "brand": 0.9},
    )
    result = validate_product(product)
    assert result.is_valid


def test_product_missing_category_is_an_error() -> None:
    product = Product(
        title="Mystery Item",
        source_image_url="https://example.com/img.jpg",
    )
    result = validate_product(product)
    assert not result.is_valid
    assert any("Category" in e for e in result.errors)


def test_product_low_confidence_score_is_a_warning() -> None:
    product = Product(
        title="Keyboard",
        category="Electronics",
        source_image_url="https://example.com/img.jpg",
        confidence_scores={"brand": 0.1},
    )
    result = validate_product(product, confidence_threshold=0.5)
    assert result.is_valid
    assert any("brand" in w for w in result.warnings)


def test_product_invalid_gtin_is_an_error() -> None:
    product = Product(
        title="Keyboard",
        category="Electronics",
        gtin="not-a-gtin",
        source_image_url="https://example.com/img.jpg",
    )
    result = validate_product(product)
    assert not result.is_valid
