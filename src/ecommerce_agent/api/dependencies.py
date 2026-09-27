from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ecommerce_agent.agent.orchestrator import ProductAgent
from ecommerce_agent.auth import SESSION_COOKIE_NAME
from ecommerce_agent.db import repository
from ecommerce_agent.db.session import get_session_factory
from ecommerce_agent.integrations.stripe_payments import PaymentGateway, StripePaymentGateway
from ecommerce_agent.llm.client import (
    AnthropicLLMClient,
    AnthropicWebSearchProvider,
    LLMClient,
    SearchProvider,
)
from ecommerce_agent.models.user import User


def get_db_session() -> Iterator[Session]:
    """A fresh session per request, closed when the request ends. Never a shared/global
    instance - concurrent requests each get their own."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@lru_cache
def get_llm_client() -> LLMClient:
    """One shared Anthropic client for the app's lifetime.

    Unlike the DB session, this is a thin, stateless HTTP client wrapper - safe to reuse
    across requests. Credentials come from `Settings` (via `AnthropicLLMClient.__init__`),
    never hardcoded here.
    """
    return AnthropicLLMClient()


@lru_cache
def get_search_provider() -> SearchProvider:
    return AnthropicWebSearchProvider()


def get_product_agent(
    session: Session = Depends(get_db_session),
    llm_client: LLMClient = Depends(get_llm_client),
    search_provider: SearchProvider = Depends(get_search_provider),
) -> ProductAgent:
    return ProductAgent(llm_client=llm_client, search_provider=search_provider, session=session)


def get_payment_gateway() -> PaymentGateway:
    """Cheap to build (it only holds credentials from `Settings`); tests override it with a fake."""
    return StripePaymentGateway()


def get_current_user(
    request: Request, session: Session = Depends(get_db_session)
) -> User:
    """The logged-in user, from the `session_token` cookie. 401 if missing/unknown/expired."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = repository.find_user_by_session_token(session, token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return user
