# CivicLens Operator Guide

Last reviewed: April 9, 2026

This guide explains how the app actually runs today, where data lives, and what to touch when something breaks.

## The Three Things You Run

### 1. Meeting pipeline

`main.py` downloads and processes meeting data:

- scrapes Granicus clips
- downloads audio, agendas, and minutes
- transcribes audio
- generates summaries and extracted facts
- writes per-meeting artifacts under `MEETINGS_OUTPUT_DIR/clips/{clip_id}`

Typical commands:

```bash
uv run python main.py --scrape --max 10
uv run python main.py --generate-index
uv run python -m api.ingest --all
```

### 2. API server

`api/server.py` is the main FastAPI app:

- loads auth, audit, analytics, search, exports, and optional modules on startup
- serves `/api/v1/*` endpoints
- lazily loads Chroma and clip metadata for RAG
- starts the in-process scheduler if that module is available

Typical command:

```bash
uv run uvicorn api.server:app --reload --port 8000
```

### 3. Frontend

`frontend/` is a React SPA that talks to the API and renders:

- meeting browser
- Q&A and chat
- votes and analytics
- admin, onboarding, usage, highlights, reports, sentiment, and calendar views

Typical commands:

```bash
cd frontend
npm install
npm run dev
```

## Where State Lives

The operational center of the app is `MEETINGS_OUTPUT_DIR`.

Important directories:

- `clips/`: meeting artifacts, summaries, transcripts, agenda text, minutes, and metadata
- `chroma_db/`: vector index for RAG retrieval
- `exports/`: generated export bundles
- `backups/`: local backup archives if backup flows are used

Important SQLite files:

- `tenants.db`
- `audit.db`
- `analytics.db`
- `saved_searches.db`
- `search.db`
- `exports.db`
- `scheduler.db`
- `sso.db`
- `branding.db`
- `webhooks.db`
- `feature_flags.db`
- `tracker.db`
- `status.db`
- `calendar.db`
- `push.db`
- `health.db`
- `highlights.db`
- `sentiment.db`
- `backups.db`
- `slack_configs.db`
- `teams_configs.db`
- `zapier.db`

There is also a newer unified database path in `api/database.py` using `civiclens.db` or PostgreSQL, but much of the runtime still uses the per-module SQLite files above.

## What Happens On A Normal Query

1. The client calls `/api/v1/ask` or `/api/v1/chat`.
2. `api/auth.py` resolves the tenant from API key or JWT.
3. `api/query.py` embeds the question and queries Chroma.
4. Matching clips are combined with clip metadata from `MEETINGS_OUTPUT_DIR/clips`.
5. The model synthesizes an answer with citations and source links.
6. `api/server.py` adds request IDs, security headers, audit logging, and response metadata.

## Saved-Search Alerts

- Saved-search alert state lives in `saved_searches.db`.
- The in-process scheduler polls due saved-search alerts every `SAVED_SEARCH_ALERTS_INTERVAL_MINUTES`.
- Delivery requires SMTP to be configured via `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, and `SMTP_FROM`.
- If `APP_BASE_URL` is set, alert emails include a direct link back to `/saved-searches`.
- API-key-only saved searches can still be created, but alert email delivery requires a saved user email from an SSO-backed session.
- Alerts are cadence-based on the last alert check, not only the last successful send. This prevents zero-result searches or missing-email searches from being retried every 10 minutes.
- `meeting_search` and `vote_search` alerts with zero matches are suppressed instead of sending low-signal email. The scheduler still records the check and waits until the next daily/weekly window.
- `rag_ask` and `rag_chat` alerts remain quota-safe: the scheduler does not auto-run a fresh LLM answer for email delivery.
- Use `GET /api/v1/saved-searches/alerts/status` to inspect per-search alert health for the current tenant. The response includes:
  - `last_alert_checked_at`
  - `last_alert_sent_at`
  - `last_alert_status`
  - `last_alert_match_count`
  - `last_alert_error`
  - `next_due_at`
  - `due_now`

## What Is Admin Versus Tenant

There are two different control planes:

- platform admin: `ADMIN_API_KEY` for cross-tenant routes such as `/api/v1/admin/tenants`
- tenant admin: a tenant API key, or an SSO browser session with the `admin` role for SSO management routes

Important current limitation:

- tenant API keys are coarse-grained and effectively act as full-tenant credentials
- RBAC is not yet enforced across every tenant-scoped configuration route

## Common Operator Tasks

### Create a tenant

Use either:

```bash
python -m api.tenants create --id elk-grove-ca --name "City of Elk Grove" --granicus-host elkgrove.granicus.com
```

or:

```bash
curl -X POST http://localhost:8000/api/v1/admin/tenants \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -d '{"id":"elk-grove-ca","name":"City of Elk Grove","granicus_host":"elkgrove.granicus.com","plan":"starter"}'
```

### Rotate a tenant API key

```bash
python -m api.tenants rotate-key --id elk-grove-ca
```

or:

```bash
curl -X POST http://localhost:8000/api/v1/admin/tenants/elk-grove-ca/rotate-key \
  -H "X-API-Key: $ADMIN_API_KEY"
```

### Check server health

```bash
curl http://localhost:8000/health
curl -H "X-API-Key: $TENANT_API_KEY" http://localhost:8000/api/v1/health
```

### Rebuild search and retrieval data

```bash
uv run python -m api.ingest --all
uv run python main.py --generate-index
```

## What Is Safe For Production Today

Reasonable today:

- local development
- single-node internal deployment
- controlled pilots with persistent disk and disciplined key handling

Not fully hardened yet:

- multi-instance web scaling with shared state
- queue-backed long-running jobs
- broad RBAC coverage
- a fully centralized system of record

For the architecture gaps, read `ARCHITECTURE_REVIEW.md`.
