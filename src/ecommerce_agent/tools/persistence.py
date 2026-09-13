from sqlalchemy.orm import Session

from ecommerce_agent.db import repository
from ecommerce_agent.models.product import Product, ProductDraft, ValidationStatus
from ecommerce_agent.tools.validation import ValidationResult


def save_product(
    session: Session,
    draft: ProductDraft,
    *,
    extraction_model: str | None = None,
) -> Product:
    """Flatten a draft that has passed validation into a Product and persist it as valid."""
    product = Product.from_draft(draft, extraction_model=extraction_model)
    product.validation_status = ValidationStatus.VALID
    product.validation_errors = []
    return repository.save_product(session, product)


def flag_for_human_review(
    session: Session,
    draft: ProductDraft,
    validation: ValidationResult,
    *,
    extraction_model: str | None = None,
) -> Product:
    """Persist a draft that could not be fully validated, marked NEEDS_REVIEW with its errors.

    The record is still saved (not dropped) so it appears in a reviewable queue instead of
    silently disappearing.
    """
    product = Product.from_draft(draft, extraction_model=extraction_model)
    product.validation_status = ValidationStatus.NEEDS_REVIEW
    product.validation_errors = validation.errors
    return repository.save_product(session, product)
