from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    database_url: str = "sqlite:///./ecommerce_agent.db"

    google_service_account_file: str | None = None

    # "Sign in with Google" (OAuth/OIDC) - unrelated to the Drive service account above. The
    # redirect URI is derived from frontend_base_url: {frontend_base_url}/auth/google/callback.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None

    notion_api_key: str | None = None
    notion_source_db_id: str | None = None
    notion_target_db_id: str | None = None

    # Stripe Checkout, test mode only - live keys are rejected (see integrations/stripe_payments.py).
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    # Where Stripe sends the browser back to after checkout.
    frontend_base_url: str = "http://localhost:8000"

    max_agent_iterations: int = 6
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)

    app_env: str = "development"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
