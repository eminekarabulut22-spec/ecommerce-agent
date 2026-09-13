from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ecommerce_agent.db.base import Base
from ecommerce_agent.db import models  # noqa: F401  (registers ProductORM on Base.metadata)
from ecommerce_agent.models.product import Product


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, expire_on_commit=False)
    db_session = session_local()
    try:
        yield db_session
    finally:
        db_session.close()
        engine.dispose()


@pytest.fixture()
def sample_product() -> Product:
    return Product(
        title="Wireless Mechanical Keyboard",
        description="A compact 65% mechanical keyboard with hot-swappable switches.",
        category="Electronics > Computer Accessories > Keyboards",
        brand="Keychron",
        manufacturer="Keychron Inc.",
        attributes={"color": "black", "material": "aluminum"},
        price=89.99,
        currency="USD",
        gtin="00012345678905",
        tags=["keyboard", "mechanical", "wireless"],
        source_image_url="https://example.com/images/keyboard.jpg",
    )
