# E-Commerce Agent

An agentic product-ingestion demo: a multimodal LLM inspects product images, decides when it needs
more information, calls tools (search, validation, database operations) to fill gaps, validates the
result, and persists a structured product record.

## Status

**Phase 6 — Backend API.** The full pipeline is implemented end to end: image upload → multimodal
extraction → an LLM-driven tool-calling agent loop (search / validate / duplicate-check /
save / flag-for-review, decided by Claude, not hardcoded control flow) → persisted, structured
product record — all exposed over a FastAPI backend (`src/ecommerce_agent/api/main.py`). No
frontend, Google Drive/Notion integrations, seed data, Docker, or auth yet.

Run it locally:

```bash
uvicorn ecommerce_agent.api.main:app --reload
python scripts/run_demo.py path/to/photo.jpg
```

## Legacy reference scripts

The four original automation scripts remain at the repo root, **unchanged**, as reference material
for the rewrite:

| File | Becomes |
|---|---|
| `ImageCaptionProcessing.py` | `tools/ingestion.py` (`fetch_image`) + `tools/extraction.py` (`extract_product_attributes`, replacing the free-text caption call) |
| `DirectServiceLinkConvert.py` | `tools/ingestion.py` (`normalize_image_link`) |
| `ViewLinkGenerating.py` | `tools/ingestion.py` (`list_source_images`) / `integrations/google_drive.py` |
| `DataTransferNotionDB.py` | `models/product.py` (field list) + `integrations/notion_export.py` (optional secondary sink) |

## Project layout

```
ecommerce-agent-new/
├── pyproject.toml
├── .env.example
├── src/ecommerce_agent/
│   ├── config.py          # pydantic-settings configuration
│   ├── models/            # Pydantic Product / ProductDraft schema (Phase 2)
│   ├── db/                # SQLAlchemy models + repository (Phase 2)
│   ├── llm/                # LLM client wrapper + prompts (Phase 3-5)
│   ├── tools/              # extraction, search, validation, duplicates, persistence (Phase 3-5)
│   ├── agent/              # LLM-driven orchestrator + trace logging (Phase 5)
│   ├── integrations/       # Google Drive, Notion adapters (not yet implemented)
│   └── api/                # FastAPI app, schemas, dependencies (Phase 6)
├── tests/
├── scripts/
│   └── run_demo.py         # CLI: post an image to a running API instance
└── data/sample_images/     # sample images for local testing (not yet populated)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# then fill in ANTHROPIC_API_KEY etc. in .env
```

Configuration is loaded via `ecommerce_agent.config.get_settings()`, backed by `pydantic-settings`,
which reads from `.env` and environment variables. See `.env.example` for every supported setting.

## Next steps

A frontend/UI, Google Drive and Notion integrations, and Docker packaging are planned but not yet
implemented. `scripts/seed_demo_products.py` seeds 5 demo products for local development (see
`scripts/README.md`).
