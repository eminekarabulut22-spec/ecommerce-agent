import pytest

from ecommerce_agent.config import Settings


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # Render's DATABASE_URL form, and the older Heroku-style alias.
        ("postgresql://u:p@host:5432/db", "postgresql+psycopg2://u:p@host:5432/db"),
        ("postgres://u:p@host/db", "postgresql+psycopg2://u:p@host/db"),
        # Query strings (e.g. sslmode for Render's external URL) are preserved.
        (
            "postgresql://u:p@host/db?sslmode=require",
            "postgresql+psycopg2://u:p@host/db?sslmode=require",
        ),
        # An explicitly chosen driver is never overridden.
        ("postgresql+psycopg2://u:p@host/db", "postgresql+psycopg2://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        # Non-Postgres URLs are untouched.
        ("sqlite:///./ecommerce_agent.db", "sqlite:///./ecommerce_agent.db"),
        ("sqlite:///:memory:", "sqlite:///:memory:"),
    ],
)
def test_database_url_pins_installed_postgres_driver(given: str, expected: str) -> None:
    assert Settings(_env_file=None, database_url=given).database_url == expected


def test_database_url_from_environment_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@render-host/db")
    assert Settings(_env_file=None).database_url == "postgresql+psycopg2://u:p@render-host/db"
