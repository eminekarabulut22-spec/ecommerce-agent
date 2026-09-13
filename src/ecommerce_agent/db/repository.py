from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ecommerce_agent.db.models import ProductORM
from ecommerce_agent.models.product import Product


def _product_to_orm_kwargs(product: Product) -> dict[str, Any]:
    data = product.model_dump()
    data["validation_status"] = product.validation_status.value
    return data


def save_product(session: Session, product: Product) -> Product:
    """Insert a new product, or update it in place if `product.id` already exists."""
    orm_product = ProductORM(**_product_to_orm_kwargs(product))
    merged = session.merge(orm_product)
    session.commit()
    session.refresh(merged)
    return Product.model_validate(merged)


def find_by_id(session: Session, product_id: UUID) -> Product | None:
    orm_product = session.get(ProductORM, product_id)
    return Product.model_validate(orm_product) if orm_product is not None else None


def find_by_gtin(session: Session, gtin: str) -> Product | None:
    """Return the product with this GTIN, or None if there isn't one.

    More than one row can legitimately share a GTIN (e.g. a flagged duplicate is saved
    alongside the original it duplicates), so this deterministically returns the oldest
    matching record - the one other rows are duplicates *of* - instead of raising when there
    is more than one match.
    """
    stmt = (
        select(ProductORM)
        .where(ProductORM.gtin == gtin)
        .order_by(ProductORM.created_at.asc(), ProductORM.id.asc())
        .limit(1)
    )
    orm_product = session.execute(stmt).scalar_one_or_none()
    return Product.model_validate(orm_product) if orm_product is not None else None


def list_products(session: Session, *, limit: int = 200) -> list[Product]:
    """Return the most recently created products first, up to `limit`."""
    stmt = select(ProductORM).order_by(ProductORM.created_at.desc()).limit(limit)
    orm_products = session.execute(stmt).scalars().all()
    return [Product.model_validate(p) for p in orm_products]


def update_product(session: Session, product_id: UUID, updates: dict[str, Any]) -> Product | None:
    """Apply a partial update to an existing product. Returns None if it doesn't exist."""
    orm_product = session.get(ProductORM, product_id)
    if orm_product is None:
        return None

    for field, value in updates.items():
        if not hasattr(orm_product, field):
            raise ValueError(f"Unknown product field: {field}")
        setattr(orm_product, field, value)

    orm_product.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(orm_product)
    return Product.model_validate(orm_product)
