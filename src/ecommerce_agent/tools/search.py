from typing import Any

from ecommerce_agent.llm.client import ProductSearchResult, SearchProvider
from ecommerce_agent.models.product import FieldConfidence, FieldSource, ProductDraft

SEARCHABLE_FIELDS = (
    "title",
    "description",
    "category",
    "brand",
    "manufacturer",
    "color",
    "material",
    "dimensions",
    "weight",
    "gtin",
)


def _draft_context(draft: ProductDraft) -> dict[str, Any]:
    return {
        name: getattr(draft, name).value
        for name in SEARCHABLE_FIELDS
        if getattr(draft, name) is not None
    }


def _best_result(results: list[ProductSearchResult]) -> ProductSearchResult | None:
    return max(results, key=lambda r: r.confidence, default=None)


def search_product_info(
    *,
    draft: ProductDraft,
    fields: list[str],
    search_provider: SearchProvider,
) -> ProductDraft:
    """Search for the given fields and merge the best findings into a copy of the draft.

    A result only overwrites an existing field if it is strictly more confident than what's
    already there, so a web search can never downgrade a value the vision extraction was
    already confident about. Fields for which nothing reliable was found are left untouched.
    """
    unknown = [name for name in fields if name not in SEARCHABLE_FIELDS]
    if unknown:
        raise ValueError(f"Unknown or non-searchable product field(s): {unknown}")

    title = draft.title.value if draft.title else "this product"
    context = _draft_context(draft)
    updated = draft.model_copy(deep=True)

    for field_name in fields:
        results = search_provider.search_product_field(
            product_title=title,
            field=field_name,
            context={k: v for k, v in context.items() if k != field_name},
        )
        best = _best_result(results)
        if best is None:
            continue

        value = best.value
        if value is None:
            continue

        current = getattr(updated, field_name)
        if current is not None and current.confidence >= best.confidence:
            continue

        setattr(
            updated,
            field_name,
            FieldConfidence(value=value, confidence=best.confidence, source=FieldSource.WEB_SEARCH),
        )

    return updated
