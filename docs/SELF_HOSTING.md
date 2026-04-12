# Self-Hosting Guide

Deploy your own CivicLens instance to process and query meetings from any Granicus-hosted government.

---

## Prerequisites

- Docker (or Python 3.9+ with uv for a non-containerized setup)
- An OpenAI API key (for transcription, embeddings, and GPT-4o synthesis)
- An Anthropic API key (optional, for Claude-powered chat and v2 summary narration)
- ffmpeg, tesseract-ocr, and poppler installed on the host (for the processing pipeline; not needed if you only run the API server)
- Access to a Granicus-hosted government video archive

---

## Quick Start with Docker

### 1. Process meetings first (on a machine with ffmpeg/tesseract)

Before building the Docker image, you need meeting data. Run the pipeline locally to download, transcribe, and index meetings:

```bash
# Clone the repo
git clone https://github.com/your-org/civiclens.git
cd civiclens

# Install dependencies
uv sync --extra api

# Set environment variables
cp .env.example .env
# Edit .env with your API keys

# Process meetings (adjust clip IDs for your Granicus instance)
uv run python main.py --scrape --max 100

# Build the RAG vector store
uv run python -m api.ingest --all

# Generate the search index
uv run python main.py --generate-index
```

### 2. Build the Docker image

The Dockerfile bakes in the processed meeting data (ChromaDB vector store and clip metadata) so the API server starts instantly:

```bash
docker build -t civiclens-api .
```

The Dockerfile expects the following directory structure:

```
meetings_output/
  chroma_db/          # ChromaDB vector store (from api.ingest)
  clips/              # Per-clip directories with metadata.json, transcripts, etc.
```

### 3. Run the container

```bash
docker run -d \
  --name civiclens \
  -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e ADMIN_API_KEY=your-admin-secret \
  -e AUTH_REQUIRED=true \
  civiclens-api
```

The server starts on port 8000. Verify it is running:

```bash
curl http://localhost:8000/health
```

### 4. Create your first tenant

```bash
curl -X POST http://localhost:8000/api/v1/admin/tenants \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-secret" \
  -d '{
    "id": "elk-grove-ca",
    "name": "City of Elk Grove",
    "granicus_host": "elkgrove.granicus.com",
    "granicus_view_id": "5",
    "plan": "pro"
  }'
```

Save the returned `api_key` -- it is the tenant's credential for all `/api/v1/` endpoints.

---

## Environment Variables

### Required

| Variable          | Description                                              |
|-------------------|----------------------------------------------------------|
| `OPENAI_API_KEY`  | OpenAI API key for embeddings (text-embedding-3-small) and GPT-4o synthesis |

### Recommended for Production

| Variable              | Description                                              | Default              |
|-----------------------|----------------------------------------------------------|----------------------|
| `ADMIN_API_KEY`       | Secret key for admin endpoints (tenant CRUD, plan management). If not set, admin endpoints return 503. | (none)               |
| `AUTH_REQUIRED`       | Set to `true` to require API keys even when no tenants exist. In dev mode (no tenants, not set), unauthenticated access is allowed. | (empty = dev mode)   |
| `MEETINGS_OUTPUT_DIR` | Path to the directory containing `chroma_db/`, `clips/`, and `tenants.db` | `./meetings_output`  |

### Optional

| Variable           | Description                                              | Default              |
|--------------------|----------------------------------------------------------|----------------------|
| `ANTHROPIC_API_KEY`| Anthropic API key for Claude-powered chat (`model_provider: "anthropic"`) and v2 summary narration | (none)               |
| `CORS_ORIGINS`     | Comma-separated list of allowed CORS origins             | `*` (allow all)      |
| `LOG_LEVEL`        | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)      | `INFO`               |
| `GRANICUS_HOST`    | Default Granicus hostname (used in dev mode when no tenant context) | (empty)              |
| `GRANICUS_VIEW_ID` | Default Granicus view ID (used in dev mode)              | (empty)              |

### Pipeline-Only Variables

These are used by `main.py` for processing meetings, not by the API server:

| Variable         | Description                                              | Default     |
|------------------|----------------------------------------------------------|-------------|
| `FIRST_CLIP_ID`  | Starting clip ID for `--auto` processing                 | `6669`      |

---

## Granicus Configuration

CivicLens works with any Granicus-powered government video archive. Each tenant is configured with:

- **`granicus_host`** -- The hostname of the Granicus instance (e.g., `elkgrove.granicus.com`, `lexington.granicus.com`). This is used to build deep-link URLs to meeting videos.
- **`granicus_view_id`** -- The Granicus view ID, which appears in video player URLs as the `view_id` parameter. Find it by navigating to any meeting video on your Granicus site and inspecting the URL.

The generated `granicus_url` in API responses follows this template:

```
https://{granicus_host}/player/clip/{clip_id}?view_id={granicus_view_id}&entrytime={timestamp}
```

To identify clips for your jurisdiction, use the probe tool:

```bash
uv run python probe_clips.py 1 7000
```

This scans clip IDs and saves valid ones to `available_clips.json`, which the pipeline uses to skip invalid IDs during processing.

---

## ChromaDB Setup

CivicLens uses ChromaDB as a local vector store for meeting embeddings. No external database is needed.

### Storage Location

The ChromaDB data is stored at `{MEETINGS_OUTPUT_DIR}/chroma_db/`. This directory is created automatically when you first run ingestion.

### Building the Index

```bash
# Ingest all processed clips
uv run python -m api.ingest --all

# Or ingest only new clips (incremental)
uv run python -m api.ingest --new

# Check index statistics
uv run python -m api.ingest --stats
```

### Rebuilding from Scratch

If you need to re-embed everything (e.g., after changing the embedding model or chunk strategy):

```bash
uv run python main.py --rebuild-rag
```

### Embedding Model

The default embedding model is `text-embedding-3-small` (1536 dimensions) from OpenAI. This is configured in `api/ingest.py`.

### What Gets Indexed

Each processed clip produces up to 5 types of chunks:

| Source Type  | Description                                              |
|-------------|----------------------------------------------------------|
| summary     | Narrative sections from the v2 summary, split on `## ` headers |
| facts       | Structured data (votes, financials, attendance) converted to searchable text |
| minutes     | Official meeting minutes split by section boundaries     |
| agenda      | Agenda text split by section boundaries                  |
| transcript  | Topic-aware chunks (~500 words) with overlap             |

Ingestion state is tracked in `{MEETINGS_OUTPUT_DIR}/rag_state.json` so incremental ingestion (`--new`) skips already-processed clips.

---

## Production Deployment

### AWS App Runner

App Runner is a straightforward option for deploying the Docker container:

1. Push your Docker image to Amazon ECR:

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin YOUR_ACCOUNT.dkr.ecr.us-east-1.amazonaws.com

docker tag civiclens-api:latest YOUR_ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/civiclens-api:latest
docker push YOUR_ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/civiclens-api:latest
```

2. Create an App Runner service:

```bash
aws apprunner create-service \
  --service-name civiclens-api \
  --source-configuration '{
    "ImageRepository": {
      "ImageIdentifier": "YOUR_ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/civiclens-api:latest",
      "ImageRepositoryType": "ECR",
      "ImageConfiguration": {
        "Port": "8000",
        "RuntimeEnvironmentVariables": {
          "OPENAI_API_KEY": "sk-...",
          "ADMIN_API_KEY": "your-admin-secret",
          "AUTH_REQUIRED": "true"
        }
      }
    },
    "AutoDeploymentsEnabled": false,
    "AuthenticationConfiguration": {
      "AccessRoleArn": "arn:aws:iam::YOUR_ACCOUNT:role/AppRunnerECRAccess"
    }
  }' \
  --instance-configuration '{
    "Cpu": "1024",
    "Memory": "2048"
  }'
```

Recommended instance size: 1 vCPU / 2 GB RAM handles typical loads. ChromaDB and clip metadata are loaded into memory on first request.

### Other Platforms

The Docker container runs anywhere that supports containers:

- **Google Cloud Run** -- Set port to 8000, min instances to 1 (cold starts load ChromaDB)
- **Azure Container Apps** -- Similar to App Runner
- **Fly.io** -- `fly launch` with the Dockerfile
- **Self-hosted** -- `docker run` behind nginx or Caddy as a reverse proxy

### Health Checks

Configure your load balancer or orchestrator to use:

- **Liveness probe:** `GET /health` (unauthenticated, lightweight)
- **Readiness probe:** `GET /health` (returns `chunks_indexed` once ChromaDB is loaded)

### Persistent Storage

The SQLite tenant database (`tenants.db`) lives inside `MEETINGS_OUTPUT_DIR`. If you run ephemeral containers, mount a persistent volume at that path or the tenant database will be lost on restart:

```bash
docker run -d \
  -v /data/civiclens:/app/meetings_output \
  -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  -e ADMIN_API_KEY=your-admin-secret \
  civiclens-api
```

### Logging

The server outputs structured JSON logs to stdout. Each log entry includes:

```json
{
  "timestamp": "2026-04-08T15:30:00+00:00",
  "level": "INFO",
  "logger": "api.server",
  "message": "request_completed",
  "request_id": "abc-123",
  "method": "POST",
  "path": "/api/v1/ask",
  "status_code": 200,
  "duration_ms": 1234.5
}
```

Pipe these to your log aggregator (CloudWatch, Datadog, etc.) for monitoring. Set `LOG_LEVEL=WARNING` in production to reduce noise.

### Updating Meeting Data

To add new meetings to a running instance:

1. Run the pipeline on a machine with ffmpeg/tesseract to process new clips
2. Run `uv run python -m api.ingest --new` to add them to ChromaDB
3. Rebuild the Docker image with the updated `meetings_output/` directory
4. Deploy the new image

For automated processing, see the Lambda handler in `lambda/sync_meetings.py` which can be triggered on a schedule via EventBridge.

---

## Tenant Management CLI

The `api.tenants` module provides a CLI for managing tenants without the API server:

```bash
# List tenants
python -m api.tenants list

# Create a tenant
python -m api.tenants create \
  --id elk-grove-ca \
  --name "City of Elk Grove" \
  --granicus-host elkgrove.granicus.com \
  --granicus-view-id 5 \
  --plan pro

# Show available plans
python -m api.tenants plans

# Rotate a tenant's API key
python -m api.tenants rotate-key --id elk-grove-ca

# Change a tenant's plan
python -m api.tenants update-plan --id elk-grove-ca --plan enterprise

# Delete a tenant
python -m api.tenants delete --id elk-grove-ca
```

Use `--output-dir` to point to a non-default data directory, or set `MEETINGS_OUTPUT_DIR`.
