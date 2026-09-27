from typing import Any

PRODUCT_EXTRACTION_SYSTEM_PROMPT = """\
You are a meticulous e-commerce product data analyst. You will be shown a single photo of a \
product and must report the attributes you can actually observe in the image.

Rules:
- Only report a field if it is visually supported by the image itself (printed text, packaging, \
  clearly depicted material/shape/color, etc.). If you cannot determine a field from the image \
  alone, omit it entirely rather than guessing.
- Never invent a brand or GTIN/barcode you cannot actually see printed or depicted.
- Do not extract, guess, or report a selling price or currency. Selling price is provided by the \
  business, not inferred from the photo. If a price tag or currency symbol is visible, ignore it \
  as a selling-price source.
- For every field you do report, include a confidence score between 0.0 and 1.0 reflecting how \
  certain you are, based purely on visual evidence in this image.
- Category should be a short breadcrumb, e.g. "Electronics > Computer Accessories > Keyboards".
- Tags should be a handful of short, lowercase keywords relevant to search/browse.

Record your findings using the provided tool. Do not respond in plain text.
"""

PRODUCT_EXTRACTION_TOOL_NAME = "record_product_draft"

PRODUCT_EXTRACTION_TOOL_DESCRIPTION = (
    "Record the product attributes visible in the image, each paired with a confidence score "
    "between 0 and 1. Omit any field that cannot be determined from the image. Do not record "
    "price or currency."
)


def _confident_string(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "value": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["value", "confidence"],
    }


PRODUCT_SEARCH_SYSTEM_PROMPT = """\
You are a product research assistant. Given a product name, a specific attribute to find, and \
any already-known details, search the web for the single most reliable, specific answer. Prefer \
manufacturer pages, major retailers, and official specification sheets over forums or unrelated \
blogs.

If you find a confident, specific answer, report it using the provided tool, including the URL \
you found it on and a confidence score reflecting how reliable and specific the source is. If you \
cannot find a reliable answer after searching, do not call the tool - just say so briefly instead.
"""

PRODUCT_SEARCH_REPORT_TOOL_NAME = "report_search_finding"

PRODUCT_SEARCH_REPORT_TOOL_DESCRIPTION = (
    "Report the single best answer found via web search for the requested product field, "
    "including a confidence score and the source URL. Only call this if you actually found "
    "a reliable answer."
)

PRODUCT_SEARCH_REPORT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "value": {
            "type": ["string", "number"],
            "description": "The value found for the requested field.",
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source_url": {"type": "string", "description": "URL of the page the value came from."},
    },
    "required": ["value", "confidence"],
}

PRODUCT_EXTRACTION_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": _confident_string("A short, human-readable product name."),
        "description": _confident_string("A one- to two-sentence description of the product."),
        "category": _confident_string("A category breadcrumb, e.g. 'Electronics > Keyboards'."),
        "brand": _confident_string("The brand name, only if visibly printed on product/packaging."),
        "manufacturer": _confident_string(
            "The manufacturer name, if distinct from the brand and visible."
        ),
        "color": _confident_string("The dominant color(s) of the product."),
        "material": _confident_string("The primary visible material, e.g. 'aluminum', 'cotton'."),
        "dimensions": _confident_string("Approximate physical dimensions, if visually estimable."),
        "weight": _confident_string("Approximate weight, only if stated on packaging."),
        "gtin": _confident_string("A barcode/GTIN number, only if clearly legible in the image."),
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short, lowercase keyword tags describing the product.",
        },
    },
    "required": ["title"],
}
