# CivicLens Product Guide

Last reviewed: April 9, 2026

## What This Project Is

CivicLens turns public meeting archives into a searchable, AI-assisted intelligence system for cities, agencies, journalists, and advocacy groups.

At a high level it does four things:

1. Pulls meeting data from Granicus.
2. Builds a structured archive of transcripts, agendas, minutes, and AI summaries.
3. Exposes that archive through an API and web app.
4. Adds SaaS features such as tenancy, billing, exports, branding, and integrations.

## The Three Main Runtime Pieces

### 1. Ingestion Pipeline

The ingestion pipeline lives in `main.py`.

It:

- downloads meeting audio or clips from Granicus
- transcribes audio with Whisper
- pulls agendas and minutes when available
- runs two-pass summary generation
- extracts structured facts such as votes, attendance, financial items, and agenda items
- writes normalized output into `meetings_output/clips/{clip_id}`

### 2. RAG / SaaS API

The backend lives in `api/`.

The main entry point is `api/server.py`, which mounts:

- Q&A and chat
- search and autocomplete
- tenants, plans, and admin flows
- analytics and audit logs
- exports and FOIA workflows
- billing, branding, and feature flags
- calendar, digest, highlights, and sentiment endpoints
- webhooks and integrations for Slack, Teams, and Zapier
- optional SSO, backup, push notifications, and compliance reporting

### 3. React Frontend

The frontend lives in `frontend/`.

The main app shell is `frontend/src/App.jsx`.

Key routes:

- `/`: meeting list
- `/meeting/:clipId`: meeting detail
- `/chat`: conversational chat over the archive
- `/ask`: single-question Q&A
- `/votes`: policy tracker and vote search
- `/analytics`: tenant analytics dashboard
- `/admin`: tenant/admin management
- `/digest`: digest subscriptions
- `/highlights`: recent and trending highlights
- `/reports`: HTML report generation
- `/usage`: usage and rate-limit views
- `/sentiment`: sentiment analysis
- `/calendar`: calendar and scheduling views
- `/onboarding`: tenant onboarding flow
- `/changelog`: product changelog

## Major Features By Category

### Archive and Retrieval

- meeting ingestion from Granicus
- transcript storage with timestamped segments
- extracted facts and structured summaries
- Chroma-based retrieval for Q&A
- full-text search with autocomplete
- Granicus deep links back to source video

### Product and UX

- searchable meeting archive
- resident-facing Q&A and chat
- multilingual UI scaffolding
- white-label branding
- embeddable widget
- onboarding flow for new tenants

### Admin and SaaS Operations

- tenant provisioning and API key rotation
- plan-based rate limiting
- billing hooks via Stripe
- usage analytics
- audit logging
- export history and FOIA request support
- feature flags
- customer health and status tracking

### Notifications and Integrations

- webhooks
- Slack integration
- Teams integration
- Zapier integration
- digest subscriptions
- calendar subscriptions
- push notification plumbing

## How The Data Flows

1. `main.py` processes meetings into `meetings_output/clips/{clip_id}`.
2. `api/ingest.py` chunks those files and stores embeddings in Chroma.
3. `api/query.py` embeds a user question, retrieves relevant chunks, and synthesizes an answer.
4. `api/server.py` adds auth, tenancy, analytics, auditing, and route handling.
5. `frontend/` calls the API and renders the archive, dashboards, and admin tools.

## What Is Stored Per Meeting

Each meeting can produce:

- `metadata.json`
- transcript text
- transcript segments JSON
- `summary.txt`
- `extracted_facts.json`
- agenda text and PDF
- minutes text and PDF

This is the source of truth for both the static archive and the RAG layer.

## The Most Important Files To Read

If you want to understand the product quickly, read these files first:

1. `README.md`
2. `main.py`
3. `api/server.py`
4. `api/query.py`
5. `frontend/src/App.jsx`
6. `docs/OPERATOR_GUIDE.md`
7. `docs/ARCHITECTURE_REVIEW.md`

## How To Run It Locally

### Pipeline only

```bash
uv sync
cp .env.example .env
uv run python main.py 6669
```

### Full app

```bash
uv sync --extra api --extra dev
uv run uvicorn api.server:app --reload --port 8000

cd frontend
npm install
npm run dev
```

### Test suite

```bash
uv run pytest tests -q
```

## What Is Real Versus Aspirational

This repo already has broad product surface area. It is not just a prototype.

What is already implemented:

- ingestion, summaries, RAG, search, tenancy, analytics, audit, exports, branding, billing hooks, and multiple integrations

What still needs enterprise hardening:

- centralized durable persistence
- production-grade background job architecture
- stronger RBAC and identity management
- clearer separation between web nodes, workers, and data services

See [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) for the detailed assessment.
