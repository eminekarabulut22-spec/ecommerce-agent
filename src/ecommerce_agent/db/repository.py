from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ecommerce_agent.db.models import (
    OrderItemORM,
    OrderORM,
    ProductORM,
    SessionORM,
    StripeWebhookEventORM,
    UserORM,
)
from ecommerce_agent.models.order import Order, OrderItem
from ecommerce_agent.models.product import Product
from ecommerce_agent.models.user import User


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


# --- Orders -------------------------------------------------------------------------------


def create_order_with_items(
    session: Session, order: Order, items: list[OrderItem]
) -> tuple[Order, list[OrderItem]]:
    """Insert an order and its line items in one transaction."""
    data = order.model_dump()
    data["status"] = order.status.value
    orm_order = OrderORM(**data)
    session.add(orm_order)
    for item in items:
        item_data = item.model_dump()
        item_data["order_id"] = orm_order.id
        session.add(OrderItemORM(**item_data))
    session.commit()
    session.refresh(orm_order)
    orm_items = (
        session.execute(select(OrderItemORM).where(OrderItemORM.order_id == orm_order.id))
        .scalars()
        .all()
    )
    return Order.model_validate(orm_order), [OrderItem.model_validate(i) for i in orm_items]


def list_order_items(session: Session, order_id: UUID) -> list[OrderItem]:
    stmt = select(OrderItemORM).where(OrderItemORM.order_id == order_id)
    orm_items = session.execute(stmt).scalars().all()
    return [OrderItem.model_validate(i) for i in orm_items]


def list_order_items_with_product_title(
    session: Session, order_id: UUID
) -> list[tuple[OrderItem, str]]:
    """Order items joined with each product's current title, for the order-detail page."""
    stmt = (
        select(OrderItemORM, ProductORM.title)
        .join(ProductORM, ProductORM.id == OrderItemORM.product_id)
        .where(OrderItemORM.order_id == order_id)
    )
    rows = session.execute(stmt).all()
    return [(OrderItem.model_validate(item), title) for item, title in rows]


def find_order_by_id(session: Session, order_id: UUID) -> Order | None:
    orm_order = session.get(OrderORM, order_id)
    return Order.model_validate(orm_order) if orm_order is not None else None


def find_order_by_checkout_session_id(session: Session, checkout_session_id: str) -> Order | None:
    stmt = select(OrderORM).where(OrderORM.stripe_checkout_session_id == checkout_session_id)
    orm_order = session.execute(stmt).scalar_one_or_none()
    return Order.model_validate(orm_order) if orm_order is not None else None


def list_orders_for_user(session: Session, user_id: UUID, *, limit: int = 200) -> list[Order]:
    stmt = (
        select(OrderORM)
        .where(OrderORM.user_id == user_id)
        .order_by(OrderORM.created_at.desc())
        .limit(limit)
    )
    orm_orders = session.execute(stmt).scalars().all()
    return [Order.model_validate(o) for o in orm_orders]


def _apply_order_updates(orm_order: OrderORM, updates: dict[str, Any]) -> None:
    for field, value in updates.items():
        if not hasattr(orm_order, field):
            raise ValueError(f"Unknown order field: {field}")
        setattr(orm_order, field, getattr(value, "value", value))
    orm_order.updated_at = datetime.now(timezone.utc)


def update_order(session: Session, order_id: UUID, updates: dict[str, Any]) -> Order | None:
    """Apply a partial update to an existing order. Returns None if it doesn't exist."""
    orm_order = session.get(OrderORM, order_id)
    if orm_order is None:
        return None
    _apply_order_updates(orm_order, updates)
    session.commit()
    session.refresh(orm_order)
    return Order.model_validate(orm_order)


def is_webhook_event_processed(session: Session, event_id: str) -> bool:
    return session.get(StripeWebhookEventORM, event_id) is not None


def record_webhook_event(
    session: Session,
    *,
    event_id: str,
    event_type: str,
    order_id: UUID | None = None,
    order_updates: dict[str, Any] | None = None,
) -> bool:
    """Mark a Stripe event as processed and apply its order changes in one transaction.

    Returns False - and changes nothing - if the event was already recorded, including when a
    concurrent delivery of the same event won the race to insert it.
    """
    session.add(StripeWebhookEventORM(event_id=event_id, event_type=event_type))
    if order_id is not None and order_updates:
        orm_order = session.get(OrderORM, order_id)
        if orm_order is not None:
            _apply_order_updates(orm_order, order_updates)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True


# --- Users and sessions ---------------------------------------------------------------------


def create_user(session: Session, user: User) -> User:
    """Insert a new user. Raises IntegrityError (caller's responsibility to catch) if the
    email is already taken."""
    orm_user = UserORM(**user.model_dump())
    session.add(orm_user)
    session.commit()
    session.refresh(orm_user)
    return User.model_validate(orm_user)


def find_user_by_email(session: Session, email: str) -> User | None:
    stmt = select(UserORM).where(UserORM.email == email)
    orm_user = session.execute(stmt).scalar_one_or_none()
    return User.model_validate(orm_user) if orm_user is not None else None


def find_user_by_id(session: Session, user_id: UUID) -> User | None:
    orm_user = session.get(UserORM, user_id)
    return User.model_validate(orm_user) if orm_user is not None else None


def create_session(session: Session, *, token: str, user_id: UUID, expires_at: datetime) -> None:
    session.add(SessionORM(token=token, user_id=user_id, expires_at=expires_at))
    session.commit()


def find_user_by_session_token(session: Session, token: str) -> User | None:
    """Returns the session's owner, or None if the token is missing/unknown/expired."""
    orm_session = session.get(SessionORM, token)
    if orm_session is None:
        return None
    expires_at = orm_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        return None
    return find_user_by_id(session, orm_session.user_id)


def delete_session(session: Session, token: str) -> None:
    orm_session = session.get(SessionORM, token)
    if orm_session is not None:
        session.delete(orm_session)
        session.commit()


def find_user_by_google_sub(session: Session, google_sub: str) -> User | None:
    stmt = select(UserORM).where(UserORM.google_sub == google_sub)
    orm_user = session.execute(stmt).scalar_one_or_none()
    return User.model_validate(orm_user) if orm_user is not None else None


def link_google_account(session: Session, user_id: UUID, google_sub: str) -> User | None:
    orm_user = session.get(UserORM, user_id)
    if orm_user is None:
        return None
    orm_user.google_sub = google_sub
    session.commit()
    session.refresh(orm_user)
    return User.model_validate(orm_user)
