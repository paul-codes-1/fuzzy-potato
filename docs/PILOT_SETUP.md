# Pilot Customer Setup Guide

Step-by-step guide for onboarding a new city as a CivicLens pilot customer.

## 1. Prerequisites

Before starting, confirm you have:

- **Python 3.9+** with [uv](https://github.com/astral-sh/uv) installed
- **ffmpeg** installed system-wide (`brew install ffmpeg` or `apt install ffmpeg`)
- **tesseract-ocr** and **poppler** (for agenda PDF OCR): `brew install tesseract poppler`
- **Node.js 18+** (for the frontend)
- **OpenAI API key** (used for Whisper transcription, GPT-4o extraction, embeddings)
- **Anthropic API key** (used for Claude Sonnet narrative summaries and chat)
- **The city's Granicus hostname and view ID** (see next section)

Copy `.env.example` to `.env` and fill in the required values:

```bash
cp .env.example .env
```

At minimum, set these:

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
ADMIN_API_KEY=<generate-a-strong-secret>
```

Install dependencies:

```bash
uv sync --extra api
```

## 2. Discovering the Granicus Host

Every city that uses Granicus for meeting video hosting has a public-facing video archive. You need two pieces of information: the **hostname** and the **view ID**.

### Finding the hostname

1. Go to the city's official website.
2. Look for links labeled "Meeting Videos," "Archived Meetings," "Video on Demand," or similar.
3. The link will take you to a page hosted on Granicus. The URL will look like:
   ```
   https://elkgrove.granicus.com/ViewPublisher.php?view_id=2
   ```
4. The hostname is the subdomain portion: `elkgrove.granicus.com`.
5. The view ID is the `view_id` query parameter: `2`.

### Verifying the host

Open this URL in a browser and confirm it loads a list of meeting videos:

```
https://{hostname}/ViewPublisher.php?view_id={view_id}
```

If the page loads and shows meeting recordings, you have the correct values.

### Common patterns

| City | Hostname | View ID |
|------|----------|---------|
| Elk Grove, CA | `elkgrove.granicus.com` | `2` |
| Lexington, KY | `lexington.granicus.com` | `2` |

View IDs vary by city. Some cities have multiple views for different meeting bodies. Start with the primary one (usually the city council).

## 3. Tenant Creation

Create the tenant using the CLI. The `--id` should be a URL-safe slug (lowercase, hyphens).

```bash
uv run python -m api.tenants create \
    --id elk-grove-ca \
    --name "Elk Grove, CA" \
    --granicus-host elkgrove.granicus.com \
    --granicus-view-id 2 \
    --plan pro
```

This will:
- Create a record in `meetings_output/tenants.db`
- Generate an API key (prefixed `mra_`) and print it to stdout
- Set the plan to `pro` (1,000 queries/month)

**Save the API key.** You will need it for verification and to deliver to the customer.

Available plans: `starter` (100 queries/mo, $299), `pro` (1,000 queries/mo, $799), `enterprise` (unlimited, $2,499).

Verify the tenant was created:

```bash
uv run python -m api.tenants list
```

## 4. Initial Data Scrape

Process the first batch of meetings. Start with 20 to validate everything works before doing a full scrape.

```bash
uv run python main.py --tenant-id elk-grove-ca --scrape --max 20
```

This will for each discovered clip:
1. Download the audio (MP3, 48kbps mono)
2. Transcribe via OpenAI Whisper
3. Download agenda and minutes PDFs (if available)
4. Extract topics via GPT-4o-mini
5. Generate structured facts via GPT-4o (pass 1)
6. Generate narrative summary via Claude Sonnet (pass 2)
7. Write all outputs to `meetings_output/clips/{clip_id}/`

**Expect ~2-5 minutes per meeting** depending on audio length and API response times.

Check the output:

```bash
ls meetings_output/clips/ | head -20
```

Spot-check a clip to make sure all files were generated:

```bash
ls meetings_output/clips/{some_clip_id}/
# Should contain: metadata.json, summary.txt, extracted_facts.json,
# transcript_*.txt, transcript_*_segments.json, and any agenda/minutes files
```

Once the first batch looks good, process the full archive:

```bash
uv run python main.py --tenant-id elk-grove-ca --scrape --max 500
```

## 5. RAG Ingestion

After meetings are processed, ingest them into the ChromaDB vector store so the Q&A system can search them.

Ingest all processed clips:

```bash
uv run python -m api.ingest --all --tenant-id elk-grove-ca
```

To ingest only clips that haven't been embedded yet (useful for incremental updates):

```bash
uv run python -m api.ingest --new --tenant-id elk-grove-ca
```

Check ingestion stats:

```bash
uv run python -m api.ingest --stats --tenant-id elk-grove-ca
```

Generate the frontend search index:

```bash
uv run python main.py --generate-index
```

## 6. Verification Checklist

### Start the API server

```bash
uv run uvicorn api.server:app --reload --port 8000
```

### Run the smoke test

```bash
API_KEY=mra_... ADMIN_KEY=<your-admin-key> ./scripts/smoke_test.sh
```

The smoke test checks:
- Public health endpoint (`GET /health`)
- Authenticated health endpoint (`GET /api/v1/health`)
- Ask endpoint (`POST /api/v1/ask`)
- Chat endpoint (`POST /api/v1/chat`)
- Search endpoint (`GET /api/v1/search`)
- Admin tenants list (`GET /api/v1/admin/tenants`)
- OpenAPI docs (`GET /docs`)

All tests should show `PASS`. If any show `FAIL`, check the server logs.

### Manual verification

Test a question against the city's data:

```bash
curl -s -X POST http://localhost:8000/api/v1/ask \
  -H "X-API-Key: mra_..." \
  -H "Content-Type: application/json" \
  -d '{"question": "What was discussed at the most recent city council meeting?"}' | python -m json.tool
```

Verify the response includes:
- An `answer` field with a relevant narrative
- A `sources` array with clip IDs and timestamps
- No errors or empty responses

Test the CLI query tool:

```bash
uv run python -m api.query "What has the city done about parks?" --tenant-id elk-grove-ca
```

### Checklist

- [ ] Tenant shows in `api.tenants list`
- [ ] At least 20 clips processed (check `meetings_output/clips/`)
- [ ] RAG ingestion completed without errors
- [ ] Smoke test passes (all 7 endpoints)
- [ ] Ask endpoint returns a relevant answer with sources
- [ ] Frontend loads and shows meeting list (`cd frontend && npm run dev`)

## 7. Go-Live Checklist

Before handing off to the customer:

- [ ] **DNS**: Point customer subdomain (e.g., `elkgrove.civiclens.ai`) to the deployment
- [ ] **SSL**: Ensure HTTPS is configured and certificates are valid
- [ ] **CORS**: Set `CORS_ORIGINS` in `.env` to the production frontend URL
- [ ] **Auth**: Set `AUTH_REQUIRED=true` in `.env` to disable dev-mode auth bypass
- [ ] **API key delivery**: Send the `mra_`-prefixed API key to the customer via a secure channel
- [ ] **Branding** (optional): Configure tenant logo, colors, and welcome message via `PUT /api/v1/branding`
- [ ] **Stripe billing**: Create a Stripe customer and subscription if billing is active
- [ ] **Full archive processed**: Run `--scrape` without `--max` or with a high limit to process the full meeting archive
- [ ] **Support contact**: Provide the customer with a support email or channel

## 8. Ongoing Operations

### Scheduled scraping

Set up automated ingestion so new meetings are processed as they appear.

Via the scheduler API:

```bash
curl -X POST http://localhost:8000/api/v1/scheduler \
  -H "X-API-Key: mra_..." \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "elk-grove-ca",
    "cron": "0 20 * * 1-5",
    "max_clips": 10
  }'
```

This runs at 8pm on weekdays and processes up to 10 new clips per run.

Alternatively, use `main.py` directly in a cron job:

```bash
# crontab entry
0 20 * * 1-5 cd /path/to/elk-grove-meetings && uv run python main.py --tenant-id elk-grove-ca --scrape --max 10 --rag
```

The `--rag` flag triggers RAG ingestion after processing, so new meetings become searchable immediately.

### Incremental RAG updates

If you process meetings without `--rag`, ingest new clips separately:

```bash
uv run python -m api.ingest --new --tenant-id elk-grove-ca
```

### Monitoring

- Check API health: `GET /health` (public, no auth needed)
- Check processing state: `meetings_output/state.json`
- Check RAG state: `meetings_output/rag_state.json`
- View usage analytics: `GET /api/v1/analytics/summary` (requires API key)
- View audit logs: `GET /api/v1/audit` (requires admin key)

### Rotating API keys

If a key is compromised:

```bash
uv run python -m api.tenants rotate-key --id elk-grove-ca
```

This generates a new key and invalidates the old one immediately.

### Updating plans

```bash
uv run python -m api.tenants update-plan --id elk-grove-ca --plan enterprise
```

### Reprocessing

To reprocess a specific meeting (e.g., after a pipeline improvement):

```bash
uv run python main.py --tenant-id elk-grove-ca 6669 --force
```

To upgrade all summaries to the latest v2 format:

```bash
uv run python main.py --tenant-id elk-grove-ca --upgrade-summaries --max 9999
```

To rebuild the entire RAG index from scratch:

```bash
uv run python main.py --tenant-id elk-grove-ca --rebuild-rag
```
