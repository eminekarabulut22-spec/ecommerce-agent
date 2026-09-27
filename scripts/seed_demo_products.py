#!/usr/bin/env python3
"""Seed the database with demo products for local development and manual testing.

The products are built entirely from local sample images in `data/sample_images/` - titles,
categories, descriptions, and prices are generic placeholders derived only from the image
filenames (no brand, model number, GTIN, or precise spec is invented). This script never calls
Anthropic or any other external API; it only writes rows to the configured database.

Usage:
    uvicorn ecommerce_agent.api.main:app --reload   # not required, but the API can read the
                                                      # same DATABASE_URL afterwards
    python scripts/seed_demo_products.py                                   # seeds DATABASE_URL from .env
    python scripts/seed_demo_products.py --database-url sqlite:///./demo.db  # seeds a specific DB

Re-running is safe: each demo product has a stable id derived from its image filename, so a
second run updates the same rows in place (via the existing upsert-by-id save) instead of
inserting duplicates.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy.orm import Session

from ecommerce_agent.db import repository
from ecommerce_agent.db.session import init_db, session_scope
from ecommerce_agent.models.product import Product
from ecommerce_agent.tools.validation import validate_product

SAMPLE_IMAGE_DIR = "data/sample_images"

# Arbitrary fixed namespace (generated once with uuid4) used to derive stable per-product ids
# from image filenames, so re-seeding upserts the same rows instead of duplicating them.
_ID_NAMESPACE = UUID("f6f6d6f0-0f9b-4a34-9f8c-6b0f7e6a0f10")


@dataclass(frozen=True)
class DemoProductSpec:
    image_filename: str
    title: str
    description: str
    category: str
    price: float
    currency: str
    tags: tuple[str, ...]


DEMO_PRODUCT_SPECS: tuple[DemoProductSpec, ...] = (
    DemoProductSpec(
        image_filename="bisiklet.jpeg",
        title="Demo Bicycle",
        description=(
            "Sample bicycle listing used for local demos and testing. Placeholder data, "
            "not a real product."
        ),
        category="Sporting Goods > Cycling > Bicycles",
        price=249.00,
        currency="USD",
        tags=("demo", "sample-data", "bicycle", "cycling"),
    ),
    DemoProductSpec(
        image_filename="buzpateni.jpeg",
        title="Demo Ice Skates",
        description=(
            "Sample pair of ice skates used for local demos and testing. Placeholder data, "
            "not a real product."
        ),
        category="Sporting Goods > Winter Sports > Ice Skates",
        price=59.00,
        currency="USD",
        tags=("demo", "sample-data", "ice-skates", "winter-sports"),
    ),
    DemoProductSpec(
        image_filename="snorkel.webp",
        title="Demo Snorkel Set",
        description=(
            "Sample snorkeling set used for local demos and testing. Placeholder data, "
            "not a real product."
        ),
        category="Sporting Goods > Water Sports > Snorkeling",
        price=29.00,
        currency="USD",
        tags=("demo", "sample-data", "snorkel", "water-sports"),
    ),
    DemoProductSpec(
        image_filename="stuhl.webp",
        title="Demo Chair",
        description=(
            "Sample chair used for local demos and testing. Placeholder data, not a real product."
        ),
        category="Home & Furniture > Seating > Chairs",
        price=89.00,
        currency="USD",
        tags=("demo", "sample-data", "chair", "furniture"),
    ),
    DemoProductSpec(
        image_filename="tisch.webp",
        title="Demo Table",
        description=(
            "Sample table used for local demos and testing. Placeholder data, not a real product."
        ),
        category="Home & Furniture > Tables",
        price=149.00,
        currency="USD",
        tags=("demo", "sample-data", "table", "furniture"),
    ),
    DemoProductSpec(
        image_filename="kiehls.jpeg",
        title="Demo Face Cream",
        description=(
            "Sample skincare cream used for local demos and testing. Placeholder data, "
            "not a real product."
        ),
        category="Beauty > Skincare > Face Cream",
        price=34.00,
        currency="USD",
        tags=("demo", "sample-data", "skincare", "cream"),
    ),
    DemoProductSpec(
        image_filename="macruj.jpeg",
        title="Demo Lip Color",
        description=(
            "Sample lip color used for local demos and testing. Placeholder data, not a real product."
        ),
        category="Beauty > Makeup > Lip Color",
        price=18.00,
        currency="USD",
        tags=("demo", "sample-data", "makeup", "lip-color"),
    ),
    DemoProductSpec(
        image_filename="parfume.jpeg",
        title="Demo Fragrance",
        description=(
            "Sample fragrance used for local demos and testing. Placeholder data, not a real product."
        ),
        category="Beauty > Fragrance",
        price=72.00,
        currency="USD",
        tags=("demo", "sample-data", "fragrance", "perfume"),
    ),
    DemoProductSpec(
        image_filename="teddy.webp",
        title="Demo Plush Toy",
        description=(
            "Sample plush toy used for local demos and testing. Placeholder data, not a real product."
        ),
        category="Toys > Stuffed Animals",
        price=22.00,
        currency="USD",
        tags=("demo", "sample-data", "plush", "toy"),
    ),
    DemoProductSpec(
        image_filename="1_org_zoom.webp",
        title="Demo Zoom Accessory",
        description=(
            "Sample accessory used for local demos and testing. Placeholder data, not a real product."
        ),
        category="Electronics > Camera Accessories",
        price=39.00,
        currency="USD",
        tags=("demo", "sample-data", "accessory", "electronics"),
    ),
)


def _stable_id(image_filename: str) -> UUID:
    return uuid5(_ID_NAMESPACE, image_filename)


def _source_image_url(image_filename: str) -> str:
    return f"{SAMPLE_IMAGE_DIR}/{image_filename}"


def build_demo_products() -> list[Product]:
    """Build the demo `Product` records, deterministic-validation-status applied.

    Pure - touches neither the database nor the network, so it's easy to unit test in isolation.
    """
    products = []
    for spec in DEMO_PRODUCT_SPECS:
        product = Product(
            id=_stable_id(spec.image_filename),
            title=spec.title,
            description=spec.description,
            category=spec.category,
            price=spec.price,
            currency=spec.currency,
            tags=list(spec.tags),
            source_image_url=_source_image_url(spec.image_filename),
        )
        validation = validate_product(product)
        product.validation_status = validation.status
        product.validation_errors = validation.errors
        products.append(product)
    return products


def seed_demo_products(session: Session) -> list[Product]:
    """Insert (or update) the demo products in the given session. Returns the saved products."""
    return [repository.save_product(session, product) for product in build_demo_products()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy database URL to seed (default: the configured DATABASE_URL / .env)",
    )
    args = parser.parse_args(argv)

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
        from ecommerce_agent.config import get_settings

        get_settings.cache_clear()

    init_db()
    with session_scope() as session:
        saved = seed_demo_products(session)

    for product in saved:
        print(f"{product.title} | id={product.id} | validation_status={product.validation_status.value}")
    print(f"\nSeeded {len(saved)} demo product(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
