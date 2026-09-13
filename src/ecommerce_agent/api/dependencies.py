from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from ecommerce_agent.agent.orchestrator import ProductAgent
from ecommerce_agent.db.session import get_session_factory
from ecommerce_agent.llm.client import (
    AnthropicLLMClient,
    AnthropicWebSearchProvider,
    LLMClient,
    SearchProvider,
)


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
