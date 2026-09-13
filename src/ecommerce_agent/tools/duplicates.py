from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ecommerce_agent.db import repository
from ecommerce_agent.models.product import Product, ProductDraft


class DuplicateCheckResult(BaseModel):
    is_duplicate: bool
    matched_product_id: UUID | None = None
    matched_by: Literal["gtin"] | None = None
    existing_product: Product | None = None


def _extract_gtin(target: ProductDraft | Product) -> str | None:
    if isinstance(target, ProductDraft):
        return target.gtin.value if target.gtin else None
    return target.gtin


def check_duplicate_product(session: Session, target: ProductDraft | Product) -> DuplicateCheckResult:
    """Check whether a product with the same GTIN already exists in the database.

    Uses the Phase 2 repository layer directly; no new persistence logic is introduced here.
    Products/drafts without a GTIN are reported as non-duplicates - there's no reliable key
    to match on yet.
    """
    gtin = _extract_gtin(target)
    if not gtin:
        return DuplicateCheckResult(is_duplicate=False)

    existing = repository.find_by_gtin(session, gtin)
    if existing is None:
        return DuplicateCheckResult(is_duplicate=False)

    return DuplicateCheckResult(
        is_duplicate=True,
        matched_product_id=existing.id,
        matched_by="gtin",
        existing_product=existing,
    )
