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

## Test payments (Stripe Checkout, test mode)

Products can be bought with a Stripe **test** card from the product detail modal. The browser
only sends a product id; the amount always comes from the product's `price`/`currency` in the
database. The order is marked `paid` only by Stripe's signed webhook
(`POST /payments/webhook`), never by the redirect back. Live (`sk_live_`) keys are refused.

| Endpoint | Purpose |
|---|---|
| `POST /payments/checkout-session` | `{product_id, quantity}` -> pending order + Stripe Checkout URL |
| `POST /payments/webhook` | Stripe events; signature-verified, idempotent by event id |
| `GET /payments/orders/{order_id}` | Order status (`pending` / `paid` / `failed`) |

```bash
alembic upgrade head                      # creates the orders + stripe_webhook_events tables
stripe listen --forward-to localhost:8000/payments/webhook   # prints STRIPE_WEBHOOK_SECRET
uvicorn ecommerce_agent.api.main:app --reload
```

Set `STRIPE_SECRET_KEY` (`sk_test_...`) and `STRIPE_WEBHOOK_SECRET` (`whsec_...`) in `.env`, then
pay with card `4242 4242 4242 4242`, any future expiry, any CVC.

## Sign in with Google

Optional, alongside email/password. OAuth 2.0 authorization code flow + OpenID Connect with
`state`, `nonce` and PKCE. The backend exchanges the code with Google and verifies the ID token
(signature, audience, issuer, expiry) server-side, requires `email_verified`, and then logs the
user in with the **same session cookie** as password login. The frontend never sends an email.

- Returning Google users are matched by Google's account ID (`users.google_sub`).
- A verified Google email matching an existing account is linked to it automatically.
- Otherwise a new user is created with no password (`password_hash` is `NULL`); password login
  for such accounts returns 401.

| Endpoint | Purpose |
|---|---|
| `GET /auth/providers` | `{"google": true/false}` - whether the button is shown |
| `GET /auth/google/login?next=cart` | Redirects to Google (`next`: `cart` / `orders` only) |
| `GET /auth/google/callback` | Google redirects back here; sets the session cookie |

Setup (Google Cloud Console -> APIs & Services):

1. **OAuth consent screen**: user type *External*, keep it in *Testing*, add your Google
   account(s) as test users. Scopes: `openid`, `email` only.
2. **Credentials -> Create OAuth client ID -> Web application**, with authorized redirect URI
   `http://localhost:8000/auth/google/callback` (i.e. `{FRONTEND_BASE_URL}/auth/google/callback`).
3. Put the client ID/secret in `.env` as `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`
   and run `alembic upgrade head`.

Open the app at the same host as `FRONTEND_BASE_URL` (`localhost`, not `127.0.0.1`) so the
cookies set before and after the Google redirect match.

## Deploy to Render (demo)

`render.yaml` is a Render Blueprint: one free web service running this FastAPI app (which also
serves `frontend/`) and one free PostgreSQL database, both in Frankfurt. Stripe stays in **test
mode**; the app refuses live keys.

- Build: `pip install -e .`. It has to be editable, because the app finds `frontend/` and
  `data/sample_images/` relative to the source tree.
- Start: `alembic upgrade head && uvicorn ecommerce_agent.api.main:app --host 0.0.0.0 --port $PORT ...`.
  Migrations run on every start (a no-op when current), since pre-deploy commands need a paid plan.
- Health check: `/health`. Python version: `.python-version`.

Steps:

1. Push the repo to GitHub, then in Render: **New -> Blueprint** and select the repo.
2. Enter the `sync: false` secrets when prompted:
   - `FRONTEND_BASE_URL`: `https://<service>.onrender.com` (no trailing slash). If the URL isn't
     known yet, enter a placeholder and fix it after the first deploy.
   - `STRIPE_SECRET_KEY` (`sk_test_...`), `STRIPE_WEBHOOK_SECRET` (step 4).
   - `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`.

   `DATABASE_URL` comes from the database automatically and `APP_ENV` is `production`. Do not set
   `ANTHROPIC_API_KEY`: without it the public `POST /products/process` returns a 502 instead of
   spending API credits.
3. **Google Cloud Console**: add the authorized redirect URI
   `https://<service>.onrender.com/auth/google/callback` to the OAuth client. While the consent
   screen is in *Testing*, only listed test users can sign in; add testers or publish the app.
4. **Stripe Dashboard (test mode) -> Developers -> Webhooks -> Add endpoint**:
   `https://<service>.onrender.com/payments/webhook` with the events
   `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`, `checkout.session.expired`. Put its signing secret in
   `STRIPE_WEBHOOK_SECRET` and redeploy. This is not the secret `stripe listen` prints locally.
5. **Seed the demo products** once, **after** the first deploy has run the migrations, from your
   machine using the database's *External* URL from the Render dashboard:

   ```bash
   python scripts/seed_demo_products.py --database-url '<External Database URL>?sslmode=require'
   ```

   Re-running updates the same rows. Don't seed before the first migration: the script's
   `create_all` would create tables Alembic then fails to create. Local SQLite users, sessions and
   orders are not copied.

Free-plan caveats: the web service sleeps after ~15 idle minutes (the first request, or a Stripe
webhook, waits for a cold start; Stripe retries), and free PostgreSQL databases expire after a
limited time. Check Render's current plan terms.

## Tests

```bash
pytest                                   # backend (Google and Stripe are mocked)
node --test 'frontend/tests/*.test.js'   # frontend helpers, Node's built-in runner, no npm deps
```

## Next steps

A frontend/UI, Google Drive and Notion integrations, and Docker packaging are planned but not yet
implemented. `scripts/seed_demo_products.py` seeds 5 demo products for local development (see
`scripts/README.md`).
