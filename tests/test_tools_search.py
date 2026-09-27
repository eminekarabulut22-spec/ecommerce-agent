from typing import Any

import pytest

from ecommerce_agent.llm.client import LLMClientError, ProductSearchResult
from ecommerce_agent.models.product import FieldConfidence, FieldSource, ProductDraft
from ecommerce_agent.tools.search import SEARCHABLE_FIELDS, search_product_info


class FakeSearchProvider:
    """Test double for SearchProvider: returns canned results per field or raises."""

    def __init__(
        self,
        responses: dict[str, list[ProductSearchResult]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._responses = responses or {}
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def search_product_field(
        self,
        *,
        product_title: str,
        field: str,
        context: dict[str, Any] | None = None,
    ) -> list[ProductSearchResult]:
        self.calls.append({"product_title": product_title, "field": field, "context": context})
        if self._error is not None:
            raise self._error
        return self._responses.get(field, [])


def _base_draft(**kwargs: Any) -> ProductDraft:
    return ProductDraft(source_image_url="https://example.com/img.jpg", **kwargs)


def test_search_fills_a_missing_field() -> None:
    draft = _base_draft(
        title=FieldConfidence(value="Keyboard X1", confidence=0.9, source=FieldSource.VISION_EXTRACTION)
    )
    provider = FakeSearchProvider(
        responses={"brand": [ProductSearchResult(field="brand", value="Keychron", confidence=0.8)]}
    )

    updated = search_product_info(draft=draft, fields=["brand"], search_provider=provider)

    assert updated.brand is not None
    assert updated.brand.value == "Keychron"
    assert updated.brand.confidence == 0.8
    assert updated.brand.source == FieldSource.WEB_SEARCH
    # original draft is untouched
    assert draft.brand is None


def test_search_does_not_downgrade_a_more_confident_existing_field() -> None:
    draft = _base_draft(
        brand=FieldConfidence(value="Keychron", confidence=0.9, source=FieldSource.VISION_EXTRACTION)
    )
    provider = FakeSearchProvider(
        responses={"brand": [ProductSearchResult(field="brand", value="Some Other Brand", confidence=0.3)]}
    )

    updated = search_product_info(draft=draft, fields=["brand"], search_provider=provider)

    assert updated.brand is not None
    assert updated.brand.value == "Keychron"
    assert updated.brand.confidence == 0.9
    assert updated.brand.source == FieldSource.VISION_EXTRACTION


def test_search_upgrades_a_less_confident_existing_field() -> None:
    draft = _base_draft(
        brand=FieldConfidence(value="Maybe Keychron", confidence=0.2, source=FieldSource.VISION_EXTRACTION)
    )
    provider = FakeSearchProvider(
        responses={"brand": [ProductSearchResult(field="brand", value="Keychron", confidence=0.85)]}
    )

    updated = search_product_info(draft=draft, fields=["brand"], search_provider=provider)

    assert updated.brand is not None
    assert updated.brand.value == "Keychron"
    assert updated.brand.confidence == 0.85
    assert updated.brand.source == FieldSource.WEB_SEARCH


def test_search_with_no_results_leaves_field_untouched() -> None:
    draft = _base_draft()
    provider = FakeSearchProvider(responses={"gtin": []})

    updated = search_product_info(draft=draft, fields=["gtin"], search_provider=provider)

    assert updated.gtin is None


def test_search_picks_the_highest_confidence_result() -> None:
    draft = _base_draft()
    provider = FakeSearchProvider(
        responses={
            "manufacturer": [
                ProductSearchResult(field="manufacturer", value="Low Confidence Co", confidence=0.2),
                ProductSearchResult(field="manufacturer", value="Keychron Inc.", confidence=0.7),
            ]
        }
    )

    updated = search_product_info(draft=draft, fields=["manufacturer"], search_provider=provider)

    assert updated.manufacturer is not None
    assert updated.manufacturer.value == "Keychron Inc."


def test_price_and_currency_are_not_web_searchable() -> None:
    assert "price" not in SEARCHABLE_FIELDS
    assert "currency" not in SEARCHABLE_FIELDS

    draft = _base_draft()
    provider = FakeSearchProvider()

    with pytest.raises(ValueError, match="non-searchable"):
        search_product_info(draft=draft, fields=["price"], search_provider=provider)
    with pytest.raises(ValueError, match="non-searchable"):
        search_product_info(draft=draft, fields=["currency"], search_provider=provider)

    assert provider.calls == []


def test_search_rejects_unknown_field_without_calling_provider() -> None:
    draft = _base_draft()
    provider = FakeSearchProvider()

    with pytest.raises(ValueError):
        search_product_info(draft=draft, fields=["not_a_real_field"], search_provider=provider)

    assert provider.calls == []


def test_search_propagates_provider_errors() -> None:
    draft = _base_draft()
    provider = FakeSearchProvider(error=LLMClientError("search backend down"))

    with pytest.raises(LLMClientError):
        search_product_info(draft=draft, fields=["brand"], search_provider=provider)


def test_search_passes_context_excluding_the_target_field() -> None:
    draft = _base_draft(
        title=FieldConfidence(value="Keyboard X1", confidence=0.9, source=FieldSource.VISION_EXTRACTION),
        category=FieldConfidence(
            value="Electronics", confidence=0.8, source=FieldSource.VISION_EXTRACTION
        ),
    )
    provider = FakeSearchProvider(responses={"brand": []})

    search_product_info(draft=draft, fields=["brand"], search_provider=provider)

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["product_title"] == "Keyboard X1"
    assert call["field"] == "brand"
    assert call["context"] == {"title": "Keyboard X1", "category": "Electronics"}
