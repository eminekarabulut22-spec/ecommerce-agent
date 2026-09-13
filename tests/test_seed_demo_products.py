from pathlib import Path

from sqlalchemy.orm import Session

from ecommerce_agent.db import repository
from ecommerce_agent.models.product import ValidationStatus
from seed_demo_products import (
    DEMO_PRODUCT_SPECS,
    build_demo_products,
    seed_demo_products,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_build_demo_products_returns_five_products() -> None:
    products = build_demo_products()

    assert len(products) == 5
    assert len(DEMO_PRODUCT_SPECS) == 5


def test_build_demo_products_reference_existing_sample_images() -> None:
    for product in build_demo_products():
        image_path = REPO_ROOT / product.source_image_url
        assert image_path.is_file(), f"missing sample image: {product.source_image_url}"


def test_build_demo_products_are_valid_with_no_invented_specifics() -> None:
    for product in build_demo_products():
        assert product.title.strip()
        assert product.category
        assert product.price is not None and product.price > 0
        assert product.currency == "USD"
        assert product.validation_status == ValidationStatus.VALID
        assert product.validation_errors == []
        # No brand, manufacturer, or GTIN was invented - the task only gave us filenames.
        assert product.brand is None
        assert product.manufacturer is None
        assert product.gtin is None


def test_build_demo_products_have_stable_deterministic_ids() -> None:
    first_run = {p.source_image_url: p.id for p in build_demo_products()}
    second_run = {p.source_image_url: p.id for p in build_demo_products()}

    assert first_run == second_run


def test_build_demo_products_have_unique_ids_and_image_urls() -> None:
    products = build_demo_products()

    assert len({p.id for p in products}) == len(products)
    assert len({p.source_image_url for p in products}) == len(products)


def test_seed_demo_products_persists_all_five(session: Session) -> None:
    saved = seed_demo_products(session)

    assert len(saved) == 5
    for product in saved:
        found = repository.find_by_id(session, product.id)
        assert found is not None
        assert found.title == product.title
        assert found.validation_status == ValidationStatus.VALID


def test_seed_demo_products_is_idempotent_on_rerun(session: Session) -> None:
    first_run = seed_demo_products(session)
    second_run = seed_demo_products(session)

    first_ids = sorted(p.id for p in first_run)
    second_ids = sorted(p.id for p in second_run)
    assert first_ids == second_ids  # re-seeding upserts the same rows, no duplicates

    for product in second_run:
        found = repository.find_by_id(session, product.id)
        assert found is not None
