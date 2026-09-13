from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from ecommerce_agent.db import repository
from ecommerce_agent.models.product import Product, ValidationStatus


def test_save_and_find_by_id_round_trips(session: Session, sample_product: Product) -> None:
    saved = repository.save_product(session, sample_product)

    found = repository.find_by_id(session, saved.id)

    assert found is not None
    assert found.id == sample_product.id
    assert found.title == sample_product.title
    assert found.gtin == sample_product.gtin
    assert found.attributes == sample_product.attributes


def test_find_by_id_returns_none_when_missing(session: Session) -> None:
    assert repository.find_by_id(session, uuid4()) is None


def test_find_by_gtin_locates_saved_product(session: Session, sample_product: Product) -> None:
    repository.save_product(session, sample_product)

    found = repository.find_by_gtin(session, sample_product.gtin)

    assert found is not None
    assert found.id == sample_product.id


def test_find_by_gtin_returns_none_when_not_found(session: Session) -> None:
    assert repository.find_by_gtin(session, "does-not-exist") is None


def test_find_by_gtin_returns_oldest_when_multiple_records_share_it(
    session: Session, sample_product: Product
) -> None:
    # A flagged duplicate is legitimately saved alongside the original it duplicates, so two
    # rows can share a GTIN. find_by_gtin must not raise MultipleResultsFound in that case,
    # and must deterministically pick one - the oldest record - regardless of insert order.
    older = sample_product.model_copy(
        update={"id": uuid4(), "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc)}
    )
    newer = sample_product.model_copy(
        update={"id": uuid4(), "created_at": datetime(2024, 6, 1, tzinfo=timezone.utc)}
    )
    repository.save_product(session, newer)  # inserted first, but is NOT the oldest by created_at
    repository.save_product(session, older)

    found = repository.find_by_gtin(session, sample_product.gtin)

    assert found is not None
    assert found.id == older.id


def test_list_products_returns_empty_list_when_none_saved(session: Session) -> None:
    assert repository.list_products(session) == []


def test_list_products_orders_newest_first(session: Session, sample_product: Product) -> None:
    older = sample_product.model_copy(
        update={"id": uuid4(), "title": "Older", "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc)}
    )
    newer = sample_product.model_copy(
        update={"id": uuid4(), "title": "Newer", "created_at": datetime(2024, 6, 1, tzinfo=timezone.utc)}
    )
    repository.save_product(session, older)
    repository.save_product(session, newer)

    results = repository.list_products(session)

    assert [p.title for p in results] == ["Newer", "Older"]


def test_list_products_respects_limit(session: Session, sample_product: Product) -> None:
    for i in range(3):
        product = sample_product.model_copy(
            update={"id": uuid4(), "created_at": datetime(2024, 1, i + 1, tzinfo=timezone.utc)}
        )
        repository.save_product(session, product)

    assert len(repository.list_products(session, limit=2)) == 2


def test_save_product_upserts_on_same_id(session: Session, sample_product: Product) -> None:
    repository.save_product(session, sample_product)

    updated = sample_product.model_copy(update={"price": 79.99})
    repository.save_product(session, updated)

    found = repository.find_by_id(session, sample_product.id)
    assert found is not None
    assert found.price == 79.99


def test_update_product_applies_partial_changes(session: Session, sample_product: Product) -> None:
    repository.save_product(session, sample_product)

    result = repository.update_product(
        session,
        sample_product.id,
        {"validation_status": ValidationStatus.VALID.value, "price": 99.0},
    )

    assert result is not None
    assert result.validation_status == ValidationStatus.VALID
    assert result.price == 99.0
    assert result.title == sample_product.title  # untouched fields survive


def test_update_product_returns_none_when_missing(session: Session) -> None:
    assert repository.update_product(session, uuid4(), {"price": 1.0}) is None


def test_update_product_rejects_unknown_field(session: Session, sample_product: Product) -> None:
    repository.save_product(session, sample_product)

    with pytest.raises(ValueError):
        repository.update_product(session, sample_product.id, {"not_a_real_field": 1})
