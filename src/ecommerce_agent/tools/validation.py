import re

from pydantic import BaseModel, Field

from ecommerce_agent.config import get_settings
from ecommerce_agent.models.product import Product, ProductDraft, ValidationStatus

_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")
_GTIN_VALID_LENGTHS = (8, 12, 13, 14)


class ValidationResult(BaseModel):
    status: ValidationStatus
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.status == ValidationStatus.VALID


def _has_valid_gtin_checksum(gtin: str) -> bool:
    # GS1 check-digit algorithm: strip the check digit, reverse the remaining digits,
    # apply alternating weights of 3 and 1 starting from the rightmost, and compare.
    digits = [int(d) for d in gtin]
    check_digit = digits[-1]
    payload = digits[:-1][::-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(payload))
    expected = (10 - total % 10) % 10
    return expected == check_digit


def _validate_gtin_format(gtin: str) -> str | None:
    if not gtin.isdigit() or len(gtin) not in _GTIN_VALID_LENGTHS:
        return f"GTIN '{gtin}' must be 8, 12, 13, or 14 digits."
    if not _has_valid_gtin_checksum(gtin):
        return f"GTIN '{gtin}' failed checksum validation."
    return None


def is_iso_currency_code(currency: str) -> bool:
    return bool(_CURRENCY_CODE_RE.match(currency))


def _validate_price_and_currency(price: float | None, currency: str | None) -> list[str]:
    errors: list[str] = []
    if price is not None and price <= 0:
        errors.append(f"Price must be greater than 0, got {price}.")
    if price is not None and currency is None:
        errors.append("Price is set but currency is missing.")
    if currency is not None and not _CURRENCY_CODE_RE.match(currency):
        errors.append(f"Currency '{currency}' must be a 3-letter uppercase ISO code.")
    return errors


def _validate_draft(draft: ProductDraft, threshold: float) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if draft.title is None or not draft.title.value.strip():
        errors.append("Title is required.")
    if draft.category is None or not draft.category.value.strip():
        errors.append("Category is required.")

    price_value = draft.price.value if draft.price else None
    currency_value = draft.currency.value if draft.currency else None
    errors.extend(_validate_price_and_currency(price_value, currency_value))

    if draft.gtin is not None:
        gtin_error = _validate_gtin_format(draft.gtin.value)
        if gtin_error:
            errors.append(gtin_error)

    for field_name in draft.low_confidence_fields(threshold):
        confidence = getattr(draft, field_name).confidence
        warnings.append(
            f"Field '{field_name}' has low confidence ({confidence:.2f} < {threshold:.2f})."
        )

    status = ValidationStatus.VALID if not errors else ValidationStatus.NEEDS_REVIEW
    return ValidationResult(status=status, errors=errors, warnings=warnings)


def _validate_flat_product(product: Product, threshold: float) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not product.category or not product.category.strip():
        errors.append("Category is required.")

    errors.extend(_validate_price_and_currency(product.price, product.currency))

    if product.gtin is not None:
        gtin_error = _validate_gtin_format(product.gtin)
        if gtin_error:
            errors.append(gtin_error)

    for field_name, confidence in product.confidence_scores.items():
        if confidence < threshold:
            warnings.append(
                f"Field '{field_name}' has low confidence ({confidence:.2f} < {threshold:.2f})."
            )

    status = ValidationStatus.VALID if not errors else ValidationStatus.NEEDS_REVIEW
    return ValidationResult(status=status, errors=errors, warnings=warnings)


def validate_product(
    target: ProductDraft | Product,
    *,
    confidence_threshold: float | None = None,
) -> ValidationResult:
    """Run deterministic business-rule validation on a `ProductDraft` or final `Product`.

    Errors are hard rule violations (missing required fields, bad price/currency, invalid
    GTIN) that mean the record should not be saved as-is. Warnings are soft signals (fields
    below the confidence threshold) that don't block saving but the orchestrator may want to
    act on, e.g. by calling `search_product_info` for that field.
    """
    threshold = (
        confidence_threshold if confidence_threshold is not None else get_settings().confidence_threshold
    )

    if isinstance(target, ProductDraft):
        return _validate_draft(target, threshold)
    return _validate_flat_product(target, threshold)
