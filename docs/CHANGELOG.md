# Changelog

All notable changes to the CivicLens API will be documented in this file.

For the current verified implementation state, prefer `PRODUCT_GUIDE.md`, `SECURITY.md`, and `ARCHITECTURE_REVIEW.md` over older release-note phrasing.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.5.0] - 2026-04-09

### Added
- **Backup/Restore system** for tenant data with point-in-time recovery metadata and optional S3 upload targets.
- **Customer Health scoring** (`/api/v1/health-scores`) with weighted signal analysis (query frequency, meeting coverage, API error rate) and churn risk detection.
- **Report Builder** (`/reports`) allowing custom PDF/CSV reports combining meeting summaries, vote histories, financial items, and sentiment trends across configurable date ranges.
- **Usage Dashboard** (`/usage`) with per-tenant bandwidth, storage, and query consumption breakdowns.
- `GET /api/v1/admin/tenants/{id}/backup` and `POST /api/v1/admin/tenants/{id}/restore` endpoints.
- `GET /api/v1/health-scores/trends` endpoint for historical health score tracking.

### Changed
- Upgraded ChromaDB to v0.5.x with improved HNSW index performance; vector queries are ~40% faster on large collections.
- Improved two-pass summary pipeline: Pass 1 now extracts resolution numbers and amendment text more reliably from OCR'd agenda PDFs.
- Admin dashboard redesigned with tabbed layout for tenant management, billing overview, and schedule configuration.

### Fixed
- Fixed race condition in concurrent RAG ingestion when multiple tenants process clips simultaneously.
- Fixed analytics event deduplication bug that inflated query counts for tenants using the Zapier polling trigger.
- Corrected timezone handling in meeting calendar iCal export for cities outside US Eastern.

### Security
- Backup and restore operations are enterprise-plan gated.
- Customer health data is tenant-scoped unless accessed through admin routes.

---

## [v1.4.0] - 2026-02-20

### Added
- **Web Push notifications** (`/api/v1/push`) with VAPID key management, per-user subscription storage, and configurable alert triggers (new meeting processed, vote detected, financial threshold exceeded).
- **Meeting Calendar** (`/calendar`) with automatic schedule detection from historical meeting patterns and iCal (.ics) feed generation.
- **Sentiment Analysis** (`/api/v1/sentiment`) for public comments using GPT-4o-mini classification into positive/negative/neutral/mixed with topic clustering and trend tracking over time.
- **Highlights generator** (`/api/v1/highlights`) that scores key moments by significance (contentious split votes, large financial appropriations >$1M, heated public comments) and returns ranked results.
- `POST /api/v1/push/subscribe` and `DELETE /api/v1/push/unsubscribe` endpoints.
- `GET /api/v1/calendar/{tenant_id}/feed.ics` for external calendar app integration.

### Changed
- Chat endpoint now supports Anthropic Claude models via the `model` parameter (e.g., `claude-sonnet-4-20250514`), selectable from the frontend ModelSelector component.
- Improved transcript chunking in `api/ingest.py` to respect sentence boundaries, reducing mid-sentence splits by 85%.
- Meeting detail page now shows sentiment badges on public comment sections.

### Fixed
- Fixed Web Push delivery failures when browser subscription endpoints returned 410 Gone (stale subscriptions now auto-cleaned).
- Fixed calendar schedule detection falsely identifying special sessions as recurring meetings.
- Resolved memory leak in sentiment batch processing when analyzing >500 comments in a single request.

### Security
- Push notification payloads no longer include full meeting text; replaced with summary snippet and deep link.

---

## [v1.3.0] - 2025-12-15

### Added
- **Slack integration** (`/api/v1/slack`) with `/civiclens ask <question>` and `/civiclens search <query>` slash commands, @CivicLens channel mentions, and Block Kit formatted responses for votes, Q&A results, and meeting summaries.
- **Microsoft Teams integration** (`/api/v1/teams`) with outgoing webhook handler for @CivicLens mentions and incoming webhook support for posting meeting summary Adaptive Cards.
- **Zapier integration** (`/api/v1/zapier`) with polling triggers (new meetings, new votes, new financial items), REST Hook instant subscriptions, and actions (RAG query, vote search, financial search, meeting export).
- **Webhook system** (`/api/v1/webhooks`) supporting five event types (`meeting.processed`, `meeting.summary_ready`, `vote.detected`, `financial_item.detected`, `query.answered`) with HMAC-SHA256 payload signing and async delivery with exponential backoff retries.
- `POST /api/v1/webhooks` CRUD for managing webhook subscriptions.
- `GET /api/v1/webhooks/{id}/deliveries` for inspecting delivery history and failure reasons.

### Changed
- RAG query responses now include a `sources` array with clip IDs and relevance scores, enabling richer integration message formatting.
- Increased default webhook retry attempts from 2 to 3 with longer backoff intervals (10s, 60s, 300s).
- Integration and webhook endpoints now return more consistent JSON responses.

### Fixed
- Fixed Slack signature verification rejecting valid requests when server clock drift exceeded 30 seconds; increased tolerance to 5 minutes.
- Fixed Zapier REST Hook unsubscribe endpoint returning 500 when the subscription had already been removed.
- Fixed webhook delivery thread pool exhaustion under high meeting processing throughput (>50 clips/hour).

---

## [v1.2.0] - 2025-10-08

### Added
- **SAML 2.0 SSO** (`/api/v1/sso`) with per-tenant IdP configuration, auto-provisioning on first login, role-based access (admin, analyst, viewer), email domain restrictions, and 8-hour JWT sessions.
- **Immutable audit logging** (`/api/v1/audit`) with SHA-256 integrity hash chains, per-request tracking (tenant, user, endpoint, status, duration), and CSV export for compliance reviews.
- **Data export** (`/api/v1/export`) with async ZIP generation containing JSON and CSV files for meetings, votes, financial items, and transcripts.
- **FOIA request handler** that uses RAG to find relevant meeting segments across the full archive and packages results as a downloadable ZIP with provenance metadata.
- `GET /api/v1/audit/logs` with filtering by tenant, user, date range, endpoint, and status code.
- `POST /api/v1/export/foia` endpoint accepting natural language FOIA descriptions.
- Per-tenant email domain allow-lists for SSO user provisioning.

### Changed
- All API responses now include `X-Request-ID` header for distributed tracing and support ticket correlation.
- SecurityHeadersMiddleware now sets restrictive security headers on API responses.
- Upgraded OpenAI SDK to v1.50+ for improved streaming reliability.

### Fixed
- Fixed JWT token refresh race condition where concurrent requests could invalidate each other's sessions.
- Fixed audit log export handling for larger result sets.
- Fixed FOIA export missing agenda PDFs when the original download URL had expired; now falls back to cached local copy.

### Security
- Audit logging uses append-only writes plus SHA-256 integrity hashes.
- SSO session tokens use HS256 signing with a dedicated `JWT_SECRET` (no longer shared with API key hashing).
- Added HSTS header on API responses.

---

## [v1.1.0] - 2025-08-01

### Added
- **Multi-tenant architecture** with tenant isolation by ID across all data paths, API keys (`mra_`-prefixed, 32-byte tokens), and configurable Granicus connection per tenant.
- **Stripe billing** (`/api/v1/billing`) with three plans: Starter ($299/mo, 100 queries), Pro ($799/mo, 1,000 queries), Enterprise ($2,499/mo, unlimited). Includes Checkout Sessions, Billing Portal, usage metering, and webhook handlers for payment events.
- **Per-tenant branding** (`/api/v1/branding`) with configurable logo, accent color, CSS overrides, welcome message, footer text, and support email.
- **Tenant management CLI** (`python -m api.tenants`) for creating, listing, deleting tenants and rotating API keys.
- **Admin API** (`/api/v1/admin/tenants`) for programmatic tenant CRUD operations.
- `GET /api/v1/admin/plans` endpoint listing available plans with query limits and pricing.
- In-memory sliding 30-day rate limiter scoped per tenant and plan.
- Self-service onboarding flow (`/onboarding`) for new tenant signup.

### Changed
- Pipeline (`main.py`) now accepts `--tenant-id` flag to scope all processing to a specific tenant's data directory.
- ChromaDB collections are now namespaced by tenant ID, enabling per-tenant vector isolation without separate databases.
- Frontend dynamically loads branding configuration on mount and applies tenant-specific theming.

### Fixed
- Fixed pipeline crash when Granicus RSS feed returned empty `<item>` elements for canceled meetings.
- Fixed rate limiter not resetting window correctly at month boundaries for tenants created mid-month.

### Security
- Tenant API keys were introduced as `mra_`-prefixed secrets with rotation support.
- Admin endpoints require a separate `ADMIN_API_KEY` distinct from tenant API keys.
- Tenant data is scoped by tenant ID across the API and ingestion paths.

---

## [v1.0.0] - 2025-06-01

### Added
- **RAG-powered Q&A** (`/api/v1/ask`) using ChromaDB vector store with `text-embedding-3-small` embeddings and GPT-4o synthesis with source citations.
- **Multi-turn chat** (`/api/v1/chat`) with conversation history and context-aware follow-up questions.
- **Meeting transcription pipeline** using OpenAI Whisper API with segment-level timestamps for video deep-linking.
- **Two-pass summary system**: Pass 1 (GPT-4o) extracts structured facts (votes, financials, attendance, public comments); Pass 2 (Claude Sonnet) generates narrative summaries with `[timestamp: MM:SS]` markers.
- **Vote tracking** with roll call extraction, per-member voting history, and searchable vote database.
- **Topic extraction** (GPT-4o-mini) generating 3-8 topics per meeting for faceted browsing.
- **Agenda and minutes download** with PDF text extraction (pdfplumber) and OCR fallback (tesseract) for scanned documents.
- **Frontend SPA** (React 18 + Vite) with meeting browser, search (FlexSearch), tabbed meeting detail view, and responsive design.
- **Search index generation** producing `index.json` for client-side full-text search across all processed meetings.
- **AWS Lambda handler** for scheduled meeting sync triggered by EventBridge (12pm and 8pm weekdays).
- `GET /health` public health check endpoint.
- Docker support with multi-stage build and `docker-compose.yml` for local development.

### Security
- CORS middleware with configurable allowed origins.
- Input sanitization on all user-facing query parameters.
- API responses stripped of internal error details in production mode.
