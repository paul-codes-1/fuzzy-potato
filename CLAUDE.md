# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**CivicLens** is a multi-tenant SaaS platform that turns Granicus-hosted government meeting archives into searchable, queryable knowledge bases. It works with any city that uses Granicus for meeting video hosting. The platform provides AI-powered transcription, structured data extraction, RAG-based Q&A, vote tracking, sentiment analysis, and integrations with Slack, Teams, and Zapier.

Each tenant represents a city/jurisdiction with its own Granicus host, API key, data isolation, branding, and billing plan.

## Reference Documentation

- **`granicus.md`** - Granicus API documentation (video player URLs, Legistar Web API, MediaManager SOAP API, RSS feeds, embed options)
- **`docs/API.md`** - Full API endpoint reference with request/response schemas
- **`docs/SECURITY.md`** - Security architecture, auth flows, compliance notes
- **`docs/SSO_SETUP.md`** - SAML 2.0 SSO configuration guide
- **`docs/SLACK_INTEGRATION.md`** - Slack app setup and slash commands
- **`docs/TEAMS_INTEGRATION.md`** - Microsoft Teams webhook/bot setup
- **`docs/ZAPIER_INTEGRATION.md`** - Zapier triggers and actions
- **`docs/EMBED_WIDGET.md`** - Embeddable widget setup for government websites
- **`docs/DATA_EXPORT.md`** - Data export and FOIA compliance
- **`docs/AUDIT_LOGGING.md`** - Audit trail and compliance (SOC 2, FISMA)
- **`docs/DATABASE.md`** - SQLite/PostgreSQL dual-backend database layer
- **`docs/SELF_HOSTING.md`** - Self-hosted deployment guide
- **`docs/QUICKSTART.md`** - Getting started guide

## Commands

```bash
# Install dependencies (using uv)
uv sync                                      # Core pipeline only
uv sync --extra api                          # Pipeline + RAG server + all modules
uv sync --extra api --extra dev              # Full dev setup with test deps
uv sync --extra api --extra postgres         # Production with PostgreSQL support

# --- Meeting Pipeline ---
uv run python main.py 6669                    # Process single clip
uv run python main.py 6669 6675               # Process range (inclusive)
uv run python main.py --auto                  # Auto-process from FIRST_CLIP_ID or last + 1
uv run python main.py --auto --max 5          # Auto-process with limit
uv run python main.py --scrape                # Scrape and process all new clips
uv run python main.py --scrape --max 100      # Scrape with limit
uv run python main.py --generate-index        # Generate search index from all clips
uv run python main.py 6669 --force            # Reprocess even if files exist
uv run python main.py 6669 --no-audio         # Don't keep audio files

# Tenant-scoped pipeline processing
uv run python main.py --tenant-id lexington-ky --scrape --max 10 --rag

# Probe available clips (without downloading)
uv run python probe_clips.py 1 7000           # Probe clip IDs 1-7000
uv run python probe_clips.py                  # Resume from last checked

# Update only minutes + summary (skip audio/transcription)
uv run python main.py 6669 --update-summary
uv run python main.py 6669 6680 --update-summary

# Backfill missing minutes/agenda for already-processed clips
uv run python main.py --backfill-docs
uv run python main.py --backfill-docs --max 50 --regenerate-summary

# Two-pass summary generation (v2)
uv run python main.py --upgrade-summaries --max 9999
uv run python main.py 44 45 --test-summary
uv run python main.py --clean-v1-summaries

# RAG ingestion
uv run python -m api.ingest --all             # Ingest all clips into vector store
uv run python -m api.ingest --new             # Ingest only new clips
uv run python -m api.ingest --clip 6669       # Ingest a specific clip
uv run python -m api.ingest --stats           # Show collection stats
uv run python main.py --rebuild-rag           # Re-embed all clips from scratch

# RAG query (CLI)
uv run python -m api.query "What has the city done about short-term rentals?"
uv run python -m api.query "budget for parks" --body Council --after 2023-01-01

# --- Tenant Management CLI ---
uv run python -m api.tenants create \
    --id elk-grove-ca \
    --name "Elk Grove, CA" \
    --granicus-host elkgrove.granicus.com \
    --granicus-view-id 2 \
    --plan pro
uv run python -m api.tenants list
uv run python -m api.tenants delete --id elk-grove-ca
uv run python -m api.tenants rotate-key --id elk-grove-ca
uv run python -m api.tenants update-plan --id elk-grove-ca --plan enterprise
uv run python -m api.tenants plans           # Show available plans and limits
uv run python -m api.tenants process --id elk-grove-ca --max 20  # Scrape + ingest for tenant

# --- API Server ---
uv run uvicorn api.server:app --reload --port 8000

# --- Frontend ---
cd frontend && npm install && npm run dev     # Dev server
cd frontend && npm run build                  # Production build

# --- Tests ---
uv run pytest tests/ -x -v                   # All Python tests
cd frontend && npm test                       # Frontend tests (Vitest)

# --- Docker ---
docker build -t civiclens .
docker run -p 8000:8000 --env-file .env civiclens
```

## Environment Variables

Set in `.env` file:

### Required
| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | OpenAI API key (transcription, summarization, embeddings, translation) |
| `ANTHROPIC_API_KEY` | Anthropic API key (v2 summary narration, chat) |

### Multi-Tenant Config
| Variable | Description |
|----------|-------------|
| `GRANICUS_HOST` | Default Granicus hostname (used by dev tenant fallback) |
| `GRANICUS_VIEW_ID` | Default Granicus view ID (used by dev tenant fallback) |
| `MEETINGS_OUTPUT_DIR` | Output directory (default: `./meetings_output`) |
| `ADMIN_API_KEY` | Admin API key for tenant management endpoints |
| `AUTH_REQUIRED` | Set to `true` to disable dev-mode auth bypass |

### Billing (Stripe)
| Variable | Description |
|----------|-------------|
| `STRIPE_SECRET_KEY` | Stripe API secret key |
| `STRIPE_WEBHOOK_SECRET` | Webhook endpoint signing secret |
| `STRIPE_PRICE_STARTER` | Stripe price ID for starter plan ($299/mo) |
| `STRIPE_PRICE_PRO` | Stripe price ID for pro plan ($799/mo) |
| `STRIPE_PRICE_ENTERPRISE` | Stripe price ID for enterprise plan ($2,499/mo) |

### Integrations
| Variable | Description |
|----------|-------------|
| `SLACK_SIGNING_SECRET` | Slack app signing secret |
| `SLACK_CLIENT_ID` | Slack OAuth client ID |
| `SLACK_CLIENT_SECRET` | Slack OAuth client secret |
| `TEAMS_WEBHOOK_SECRET` | Teams outgoing webhook HMAC secret |

### SSO / Auth
| Variable | Description |
|----------|-------------|
| `JWT_SECRET` | Secret for signing session JWTs (generate for production) |
| `SP_BASE_URL` | SAML Service Provider base URL (default: `https://app.civiclens.ai`) |

### Email / Notifications
| Variable | Description |
|----------|-------------|
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP port (default: 587) |
| `SMTP_USERNAME` | SMTP auth username |
| `SMTP_PASSWORD` | SMTP auth password |
| `SMTP_USE_TLS` | Enable TLS (default: true) |
| `SMTP_FROM` | From address (default: `CivicLens <noreply@civiclens.io>`) |
| `VAPID_CONTACT` | Contact email for Web Push VAPID keys |

### Database / Infrastructure
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (absent = SQLite) |
| `CORS_ORIGINS` | Comma-separated allowed origins (default: `*`) |
| `LOG_LEVEL` | Logging level (default: `INFO`) |
| `CIVICLENS_BASE_URL` | Frontend base URL for email links |
| `CIVICLENS_API_URL` | API base URL for digest unsubscribe links |

## System Requirements

- Python 3.9+
- ffmpeg (system installation)
- yt-dlp (installed via uv)
- tesseract-ocr + poppler (for OCR of scanned agenda PDFs)
- Node.js 18+ (for frontend)
- OpenAI API key + Anthropic API key

## Architecture

### Multi-Tenant Model

Each tenant (city/jurisdiction) is identified by a unique ID and has:
- **Granicus connection** (`granicus_host`, `granicus_view_id`) for video/clip access
- **API key** (`mra_`-prefixed, 32-byte token) for programmatic access
- **Plan** (starter: 100 queries/mo, pro: 1,000, enterprise: unlimited)
- **Isolated data** in `meetings_output/{tenant_id}/` directories
- **Per-tenant branding** (logo, colors, welcome message, footer)
- **Per-tenant SSO** (SAML 2.0 with configurable IdP)
- **Per-tenant scheduling** (cron-based automated meeting ingestion)

Tenant records stored in SQLite (`tenants.db`) with Stripe billing fields.

### Authentication Layer (`api/auth.py`)

Three auth methods, checked in order:
1. **X-API-Key header** -- for programmatic / API access, looked up in `tenants.db`
2. **Authorization: Bearer JWT** -- for SSO browser sessions (signed with `JWT_SECRET`)
3. **civiclens_session cookie** -- fallback for SSO browser sessions
4. **Dev fallback** -- if no tenants exist and `AUTH_REQUIRED` is not set, returns a dev tenant with enterprise plan

Rate limiting is in-memory sliding 30-day window per tenant. Admin endpoints require a separate `ADMIN_API_KEY`.

### SSO (`api/sso.py`)

SAML 2.0 support via `python3-saml`:
- Per-tenant IdP configuration (entity ID, SSO URL, X.509 cert, SLO URL)
- Roles: admin, analyst, viewer with permission sets
- JWT session tokens (8-hour expiry, HS256 signed)
- User lifecycle with auto-provisioning on first SAML login
- Email domain restrictions per tenant

### Backend Pipeline (`main.py`)

Single-file pipeline with `LFUCGPipeline` class (accepts `--tenant-id` for multi-tenant scoping):

1. **Title Extraction** -- yt-dlp
2. **Metadata Scraping** -- date and meeting body from title
3. **Download** -- MP3 (48kbps, 22kHz mono)
4. **Compression** -- ffmpeg if >24MB
5. **Transcription** -- OpenAI Whisper API with segment timestamps
6. **Agenda Download** -- PDF extraction (pdfplumber, OCR fallback)
7. **Minutes Download** -- PDF or HTML minutes
8. **Topic Extraction** -- gpt-4o-mini, 3-8 topics
9. **Summary Generation** -- Two-pass system (see below)
10. **Index Generation** -- searchable index.json for frontend

### Two-Pass Summary System (`summary_v2.py`)

- **Pass 1 (GPT-4o)**: Structured JSON extraction -- votes, roll calls, financial items, attendance, agenda items, public comments, appointments, contentious items. Saved as `extracted_facts.json`. Temperature 0.1, `response_format=json_object`.
- **Pass 2 (Claude Sonnet)**: Section-by-section narrative from extracted facts. Conditional sections, `[timestamp: MM:SS]` markers for video deep-linking.

### RAG Q&A System (`api/`)

Core modules (always loaded):
- **`api/ingest.py`** -- Chunks clips into 5 source types (summary, facts, minutes, agenda, transcript) for ChromaDB embedding via `text-embedding-3-small`
- **`api/query.py`** -- Retrieval + LLM synthesis with citations. Supports both `ask()` (single-turn) and `chat()` (multi-turn with OpenAI or Anthropic)
- **`api/auth.py`** -- Tenant store, rate limiter, API key + JWT auth
- **`api/server.py`** -- FastAPI app with middleware stack (security headers, request IDs, audit logging, CORS)
- **`api/analytics.py`** -- SQLite-backed usage tracking (queries, meetings processed, API calls)
- **`api/audit.py`** -- Immutable append-only audit log (SOC 2 / FISMA compliant)
- **`api/billing.py`** -- Stripe integration (customer creation, subscriptions, checkout, portal, webhooks, usage metering)
- **`api/branding.py`** -- Per-tenant white-label config (logo, colors, CSS, welcome message)
- **`api/export.py`** -- Async data export (ZIP with JSON/CSV) and FOIA request handler
- **`api/search.py`** -- SQLite FTS5 full-text search (meetings, votes, financial items, speakers, topics)
- **`api/database.py`** -- Unified database layer (SQLite for dev, PostgreSQL for production)

Optional modules (graceful degradation if deps missing):
- **`api/scheduler.py`** -- APScheduler-based per-tenant cron jobs for automated ingestion
- **`api/webhooks.py`** -- Webhook subscriptions with async delivery, HMAC signing, retry with exponential backoff
- **`api/sentiment.py`** -- Public comment sentiment analysis (GPT-4o-mini classification, topic clustering, trend tracking)
- **`api/highlights.py`** -- Meeting highlights generator (scored key moments: contentious votes, large financial items, public comments)
- **`api/digest.py`** -- Email digest system (daily/weekly, CAN-SPAM compliant, double opt-in)
- **`api/push.py`** -- Web Push notifications (VAPID, per-user subscription management)
- **`api/tracker.py`** -- Policy/vote tracker with alert rules (keyword, member, financial, vote triggers)
- **`api/calendar.py`** -- Meeting calendar with schedule detection and iCal generation
- **`api/translate.py`** -- GPT-4o translation (en, es, zh, vi, ko) preserving citations and formatting
- **`api/status.py`** -- Status page (component health, uptime tracking, incident management)
- **`api/customer_health.py`** -- Customer health scoring (0-100, weighted signals, churn detection)
- **`api/notifications.py`** -- Email notification system (SMTP)
- **`api/sso.py`** -- SAML 2.0 SSO (see above)
- **`api/cost.py`** -- Per-tenant LLM cost attribution (SQLite `costs.db`, OpenAI + Anthropic pricing table, admin + tenant self-view routes via `api/cost_routes.py`)
- **`api/query_cache.py`** -- SQLite-backed query response cache with TTL and optional semantic similarity matching
- **`api/leads.py`** -- Lead capture store (SQLite, email dedupe, IP hashing, intent classification)
- **`api/saved_searches.py`** -- Saved search CRUD with alert scheduling (daily/weekly frequency)
- **`api/feature_flags.py`** -- Per-tenant feature flag management
- **`api/compliance.py`** -- Compliance reporting (SOC 2, data residency checks)
- **`api/metrics.py`** -- Prometheus-style metrics collection
- **`api/reports.py`** -- Report generation (meeting summaries, analytics exports)
- **`api/backup.py`** -- Data backup engine (tenant-scoped, scheduled)
- **`api/diff.py`** -- Meeting diff engine (compare changes across versions)
- **`api/onboarding_emails.py`** -- Automated onboarding email sequences
- **`api/integrations/slack.py`** -- Slack bot (slash commands, @mentions, Block Kit messages)
- **`api/integrations/teams.py`** -- Teams bot (webhooks, Adaptive Cards)
- **`api/integrations/zapier.py`** -- Zapier integration (polling triggers, REST Hooks, actions)

### Frontend (`frontend/`)

React 18 SPA with Vite, React Router, and i18n (en + es fully translated; zh, vi, ko with partial coverage -- common UI translated confidently, a small set of specialized civic/legal terms like `ordinance`/`appropriation`/`proclamation` fall back to English, see `frontend/src/i18n/locales/{zh,vi,ko}.todo.md`). Locale drift is guarded by `frontend/src/i18n/__tests__/locale-coverage.test.js`.

### Frontend Routes

| Route | Component | Description |
|-------|-----------|-------------|
| `/` | `MeetingList` | Browse/search meetings with FlexSearch |
| `/meeting/:clipId` | `MeetingDetail` | Tabbed view: Overview, Transcript, Agenda, Minutes |
| `/chat` | `ChatMeetings` | Multi-turn RAG chat (OpenAI/Anthropic model selector) |
| `/ask` | `AskQuestion` | Single-turn RAG Q&A with filters |
| `/votes` | `VoteTracker` | Vote search and policy alert management |
| `/analytics` | `AnalyticsDashboard` | Usage analytics and query trends |
| `/admin` | `AdminDashboard` | Tenant management, billing, schedules |
| `/admin/health` | `CustomerHealth` | Customer health scores and churn risk |
| `/onboarding` | `Onboarding` | Self-service tenant signup flow |
| `/digest` | `DigestSubscribe` | Email digest subscription management |
| `/highlights` | `Highlights` | Meeting highlights browser |
| `/whats-new` | `MeetingDiff` | What's changed between meeting versions |
| `/reports` | `ReportBuilder` | Custom report generation |
| `/usage` | `UsageDashboard` | API usage and quota tracking |
| `/sentiment` | `SentimentDashboard` | Public comment sentiment analysis |
| `/calendar` | `MeetingCalendar` | Calendar view of scheduled meetings |
| `/changelog` | `Changelog` | Platform release notes |
| `/saved-searches` | `SavedSearches` | Saved search management with alerts |

Additional components (embedded in pages, not standalone routes): `GlobalSearch`, `LanguageSwitcher`, `ModelSelector`, `SaveSearchButton`, `A11yAnnouncer`, `ErrorBoundary`, `LeadsDashboard`.

### Embeddable Widget (`widget/`)

Drop-in `<script>` tag for government websites. Configurable via `data-` attributes:
- `data-api-key`, `data-api-url` (required)
- `data-theme` (light/dark), `data-position`, `data-accent-color`, `data-welcome-message`, `data-title`

### Client SDKs (`sdk/`)

- **JavaScript** (`sdk/javascript/`) -- npm package wrapping the CivicLens API
- **Python** (`sdk/python/`) -- pip-installable client library

### AWS Lambda (`lambda/`)

Lambda handler for scheduled meeting sync:
- Triggered by EventBridge (12pm and 8pm weekdays)
- Processes new clips, generates index, syncs to S3

### Infrastructure (`infra/`)

Terraform configuration for AWS deployment (environments, variables, outputs).

## API Endpoints

All authenticated endpoints require `X-API-Key` header or JWT Bearer token. Admin endpoints require `ADMIN_API_KEY`.

### Core Q&A
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/ask` | Single-turn RAG Q&A |
| POST | `/api/v1/chat` | Multi-turn conversational chat |
| GET | `/api/v1/health` | Authenticated health check |
| GET | `/health` | Public health check (no auth) |

### Admin / Tenant Management
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/admin/tenants` | List all tenants |
| POST | `/api/v1/admin/tenants` | Create tenant |
| DELETE | `/api/v1/admin/tenants/{id}` | Delete tenant |
| POST | `/api/v1/admin/tenants/{id}/rotate-key` | Rotate API key |
| PATCH | `/api/v1/admin/tenants/{id}/plan` | Change plan |
| GET | `/api/v1/admin/plans` | List plans and limits |

### Module Routes (all under `/api/v1/`)
| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `analytics_routes` | `/api/v1/analytics` | Usage summary, queries over time, top questions, admin overview |
| `audit_routes` | `/api/v1/audit` | Query audit logs, export CSV |
| `billing_routes` | `/api/v1/billing` | Checkout, portal, usage, plan changes, Stripe webhooks |
| `branding_routes` | `/api/v1/branding` | Get/update tenant branding |
| `export_routes` | `/api/v1/export` | Data export jobs, FOIA requests, download |
| `search_routes` | `/api/v1/search` | Full-text search, autocomplete |
| `scheduler_routes` | `/api/v1/scheduler` | Schedule CRUD, trigger now, job history |
| `webhook_routes` | `/api/v1/webhooks` | Webhook CRUD, event types, delivery logs |
| `slack_routes` | `/api/v1/slack` | Slash commands, events, OAuth |
| `teams_routes` | `/api/v1/teams` | Outgoing webhook, config |
| `zapier_routes` | `/api/v1/zapier` | Polling triggers, REST Hook subscribe/unsubscribe, actions |
| `sso_routes` | `/api/v1/sso` | SAML login, ACS callback, metadata, user management |
| `status_routes` | `/api/v1/status` | Component health, incidents |
| `tracker_routes` | `/api/v1/tracker` | Alert CRUD, policy search, vote search |
| `push_routes` | `/api/v1/push` | Web Push subscribe/unsubscribe, VAPID public key |
| `health_routes` | `/api/v1/health-scores` | Customer health scores, trends |
| `highlights_routes` | `/api/v1/highlights` | Meeting highlights, top highlights |

Widget endpoints: `POST /api/v1/widget/ask`, `POST /api/v1/widget/chat` (same auth, widget-optimized).

Legacy paths (`/api/ask`, `/api/chat`, `/ask`, `/chat`) are preserved for backward compatibility.

See `docs/API.md` for full request/response schemas. Interactive docs at `/docs` (Swagger UI) and `/redoc`.

## Middleware Stack

Applied in order (outermost first):
1. **AuditMiddleware** -- logs every request to immutable audit trail
2. **RequestIDMiddleware** -- assigns `X-Request-ID` to every request, logs timing
3. **SecurityHeadersMiddleware** -- HSTS, CSP, X-Frame-Options, etc.
4. **CORSMiddleware** -- configurable via `CORS_ORIGINS`

## Billing

Stripe-powered subscription billing (`api/billing.py`):
- Plans: starter ($299/mo, 100 queries), pro ($799/mo, 1,000 queries), enterprise ($2,499/mo, unlimited)
- Checkout Sessions for self-service signup
- Billing Portal for customer self-management
- Usage metering via Stripe subscription items
- Webhook handlers: `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.deleted`, `customer.subscription.updated`, `checkout.session.completed`
- Auto-downgrade to starter on subscription cancellation

## Integrations

### Slack (`api/integrations/slack.py`)
- Slash commands: `/civiclens ask <question>`, `/civiclens search <query>`
- @CivicLens mentions in channels trigger RAG Q&A
- Incoming webhooks for meeting summary notifications
- Block Kit message formatting for votes, Q&A, search results

### Microsoft Teams (`api/integrations/teams.py`)
- Outgoing webhook handler for @CivicLens mentions
- Incoming webhook for posting meeting summaries
- Adaptive Card formatters for meetings, votes, Q&A

### Zapier (`api/integrations/zapier.py`)
- Polling triggers: new meetings, new votes, new financial items
- REST Hook subscriptions for instant triggers
- Actions: RAG query, vote search, financial search, meeting export
- Flat JSON responses with unique `id` fields per Zapier requirements

### Webhooks (`api/webhooks.py`)
- Events: `meeting.processed`, `meeting.summary_ready`, `vote.detected`, `financial_item.detected`, `query.answered`
- HMAC-SHA256 payload signing
- Async delivery with exponential backoff (3 retries)
- Per-webhook delivery log

## State Management

- **Tenant database**: `meetings_output/tenants.db` (SQLite)
- **Analytics**: `meetings_output/analytics.db`
- **Audit log**: `meetings_output/audit.db` (append-only)
- **Scheduler**: `meetings_output/scheduler.db` (configs + job history)
- **SSO**: `meetings_output/sso.db` (IdP configs + users)
- **Pipeline state**: `meetings_output/state.json`
- **RAG ingestion state**: `meetings_output/rag_state.json`
- **Vector store**: `meetings_output/chroma_db/` (ChromaDB)
- **VAPID keys**: `meetings_output/vapid_private.pem`
- **PostgreSQL** (optional): set `DATABASE_URL` for production multi-instance deployments

## Output Structure

```
meetings_output/
  tenants.db                              # Tenant records + API keys + Stripe fields
  analytics.db                            # Usage analytics events
  audit.db                                # Immutable audit log
  scheduler.db                            # Schedule configs + job history
  sso.db                                  # SSO configs + user records
  state.json                              # Pipeline state
  index.json                              # Search index for frontend
  available_clips.json                    # Probed clip IDs (from probe_clips.py)
  rag_state.json                          # RAG ingestion state
  chroma_db/                              # ChromaDB vector store
  vapid_private.pem                       # Web Push VAPID private key
  exports/                                # Async export job output (ZIP files)
  clips/
    {clip_id}/
      metadata.json                       # Processing metadata
      summary.txt                         # AI narrative summary (v2)
      extracted_facts.json                # Structured extraction (votes, amounts, etc.)
      transcript_*.txt                    # Whisper transcription
      transcript_*_segments.json          # Timestamped segments
      *_audio.mp3                         # Downloaded audio
      *_agenda_*.pdf / .txt               # Meeting agenda
      *_minutes_*.pdf / .html / .txt      # Official minutes

api/
  __init__.py
  server.py                               # FastAPI app + middleware + endpoints
  auth.py                                 # Tenant store + rate limiter + auth deps
  tenants.py                              # Tenant management CLI + TenantManager
  query.py                                # RAG retrieval + LLM synthesis
  ingest.py                               # Chunking + embedding + ChromaDB
  prompts.py                              # System prompts for synthesis
  analytics.py                            # Usage analytics (SQLite)
  analytics_routes.py
  audit.py                                # Immutable audit log
  audit_routes.py
  billing.py                              # Stripe billing integration
  billing_routes.py
  branding.py                             # Per-tenant white-label config
  branding_routes.py
  calendar.py                             # Meeting calendar + iCal
  calendar_routes.py
  customer_health.py                      # Health scoring + churn detection
  database.py                             # SQLite/PostgreSQL unified layer
  digest.py                               # Email digest (daily/weekly)
  digest_routes.py
  export.py                               # Data export + FOIA
  export_routes.py
  highlights.py                           # Meeting highlights generator
  highlights_routes.py
  logging_config.py                       # Structured JSON logging
  notifications.py                        # Email notifications (SMTP)
  openapi_config.py                       # OpenAPI schema customization
  push.py                                 # Web Push notifications (VAPID)
  push_routes.py
  scheduler.py                            # APScheduler per-tenant cron
  scheduler_routes.py
  search.py                               # SQLite FTS5 full-text search
  search_routes.py
  sentiment.py                            # Comment sentiment analysis
  sentiment_routes.py
  sso.py                                  # SAML 2.0 SSO
  sso_routes.py
  status.py                               # Status page + uptime
  status_routes.py
  tracker.py                              # Policy/vote tracker + alerts
  tracker_routes.py
  translate.py                            # GPT-4o translation service
  webhook_routes.py
  webhooks.py                             # Webhook subscriptions + delivery
  integrations/
    slack.py                              # Slack bot + slash commands
    slack_routes.py
    teams.py                              # Teams webhooks + Adaptive Cards
    teams_routes.py
    zapier.py                             # Zapier triggers + actions
    zapier_routes.py
  migrations/
    001_initial.py
    002_digest_subscribers.py

frontend/
  src/
    App.jsx                               # Router + layout + i18n + branding
    components/
      MeetingList.jsx                     # Browse/search meetings
      MeetingDetail.jsx                   # Tabbed: Overview/Transcript/Agenda/Minutes
      ChatMeetings.jsx                    # Multi-turn RAG chat
      AskQuestion.jsx                     # Single-turn RAG Q&A
      VoteTracker.jsx                     # Vote search + policy alerts
      AnalyticsDashboard.jsx              # Usage analytics
      AdminDashboard.jsx                  # Tenant/billing/schedule management
      CustomerHealth.jsx                  # Health scores + churn risk
      Onboarding.jsx                      # Self-service signup
      GlobalSearch.jsx                    # Cross-feature search
      LanguageSwitcher.jsx                # i18n language selector
      DigestSubscribe.jsx                 # Email digest subscription
      Highlights.jsx                      # Meeting highlights
      MeetingCalendar.jsx                 # Calendar view
      SentimentDashboard.jsx              # Sentiment analysis
      ModelSelector.jsx                   # OpenAI/Anthropic model picker
      A11yAnnouncer.jsx                   # Screen reader announcements
    hooks/
      useAdmin.js                         # Admin API hooks
      useBranding.js                      # Tenant branding
      useChat.js                          # Chat state management
      useFlexSearch.js                    # Full-text search engine
      useMeetings.js                      # Meeting data fetching
      useOnboarding.js                    # Onboarding flow
      usePushNotifications.js             # Web Push subscription
      useSearch.js                        # Search + filter logic
    i18n/
      I18nProvider.jsx                    # Translation context provider
      locales/                            # Language files (en, es, zh, vi, ko)
    contexts/
      SearchContext.jsx                   # Global search state

widget/
  civiclens-widget.js                     # Embeddable <script> tag widget
  index.html                              # Widget demo page

sdk/
  javascript/                             # npm client library
  python/                                 # pip client library

lambda/
  sync_meetings.py                        # Lambda handler for scheduled sync
  requirements.txt

infra/
  main.tf                                # Terraform AWS infrastructure
  variables.tf
  outputs.tf
  environments/

tests/
  conftest.py                             # Shared fixtures
  test_auth.py                            # Auth + rate limiting
  test_tenants.py                         # Tenant CLI + management
  test_ingest.py                          # Chunking + embedding
  test_query.py                           # Retrieval + synthesis
  test_server.py                          # FastAPI endpoints
  test_webhooks.py                        # Webhook delivery
  test_analytics.py                       # Analytics store
  test_api_e2e.py                         # End-to-end API tests
  test_rag_e2e.py                         # RAG pipeline e2e
  test_integration.py                     # main.py pipeline hooks
  test_summary_v2.py                      # Two-pass summary
  test_docker.py                          # Docker build/run tests
```

## Metadata JSON Structure

```json
{
  "clip_id": 6669,
  "url": "https://...",
  "date": "2026-01-08",
  "meeting_body": "WQFB",
  "title": "January 8 2026 WQFB meeting",
  "topics": ["Budget", "Grants", "Public Comment"],
  "files": {
    "audio": "..._audio.mp3",
    "transcript": "transcript_...txt",
    "transcript_segments": "transcript_..._segments.json",
    "summary_txt": "summary.txt",
    "extracted_facts": "extracted_facts.json",
    "agenda_pdf": "..._agenda_....pdf",
    "agenda_txt": "..._agenda_....txt",
    "minutes_pdf": "..._minutes_....pdf",
    "minutes_txt": "..._minutes_....txt"
  },
  "processed_at": "...",
  "processing_time_seconds": 120.5,
  "transcript_words": 6660,
  "audio_kept": true,
  "models": {
    "transcribe": "whisper-1",
    "summary": "gpt-4o+claude-sonnet",
    "topics": "gpt-4o-mini"
  }
}
```

## Extracted Facts JSON Structure

```json
{
  "meeting_info": {
    "date": "2026-01-08",
    "time": "6:00 PM",
    "body": "Urban County Council",
    "presiding_officer": "Mayor Linda Gorton",
    "location": "Council Chambers"
  },
  "attendance": { "present": [], "absent": [], "late": [] },
  "motions_and_votes": [{
    "identifier": "Ordinance 0016-26",
    "description": "...",
    "motion_by": "Brown",
    "second_by": "Curtis",
    "outcome": "passed",
    "vote_type": "roll_call",
    "ayes": 8, "nays": 0, "abstentions": 0,
    "votes_for": [], "votes_against": [],
    "transcript_approx_time": "25:15"
  }],
  "financial_items": [{
    "description": "General Obligation Bonds",
    "amount": "$18,040,000",
    "type": "appropriation"
  }],
  "public_comments": [{
    "speaker": "John Smith",
    "topic": "Zoning concerns",
    "summary": "...",
    "transcript_approx_time": "15:30"
  }],
  "agenda_items": [],
  "appointments": [],
  "contentious_items": []
}
```

## Compliance

- **Audit logging**: Append-only SQLite audit trail with SHA-256 integrity hashes. Every API request logged with tenant, user, endpoint, status, and duration. Designed for SOC 2 and FISMA requirements.
- **FOIA support**: `api/export.py` includes a FOIA request handler that uses RAG to find relevant meeting segments across the archive and packages results as downloadable ZIP.
- **CAN-SPAM**: Email digests use double opt-in confirmation tokens and one-click unsubscribe.
- **Data isolation**: Each tenant's data is scoped by tenant ID in queries and file paths. SSO email domain restrictions prevent cross-tenant access.
- **Security headers**: HSTS, CSP, X-Frame-Options DENY, X-Content-Type-Options nosniff on every response.
