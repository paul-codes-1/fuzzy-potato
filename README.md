# CivicLens

CivicLens is an AI-powered government meeting intelligence platform. This repo now contains two connected systems:

- A Granicus ingestion pipeline that downloads meetings, transcripts, agendas, minutes, and structured summaries.
- A multi-tenant SaaS application with a FastAPI backend, React frontend, integrations, exports, analytics, and admin tooling.

## Documentation

- **[docs/README.md](docs/README.md)** - Documentation index and repo map
- **[docs/PRODUCT_GUIDE.md](docs/PRODUCT_GUIDE.md)** - What the app does, feature inventory, and how the pieces fit together
- **[docs/OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md)** - What actually runs, where state lives, and how to operate the system today
- **[docs/ARCHITECTURE_REVIEW.md](docs/ARCHITECTURE_REVIEW.md)** - Architecture review and enterprise readiness assessment
- **[docs/QUICKSTART.md](docs/QUICKSTART.md)** - Quickstart for the SaaS/API layer
- **[docs/API.md](docs/API.md)** - API reference
- **[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md)** - Self-hosting and deployment notes
- **[docs/DATABASE.md](docs/DATABASE.md)** - Database strategy and migration notes
- **[docs/SECURITY.md](docs/SECURITY.md)** - Security architecture and controls
- **[granicus.md](granicus.md)** - Granicus platform notes, URL parameters, and source-system behavior

## Repo At A Glance

- `main.py`: meeting ingestion, transcription, summarization, and archive generation
- `api/`: FastAPI application and platform modules (auth, search, analytics, exports, billing, integrations)
- `frontend/`: React SPA for residents, staff, and admins
- `sdk/`: Python and JavaScript API clients
- `widget/`: embeddable chat widget
- `infra/`: Terraform for AWS deployment
- `marketing/`: marketing site and collateral

## Quick Start

```bash
# Install dependencies
uv sync

# Set up environment
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY and ANTHROPIC_API_KEY

# Process a single clip
uv run python main.py 6669

# Generate search index
uv run python main.py --generate-index

# Start frontend dev server
cd frontend && npm install && npm run dev
```

## Requirements

- Python 3.9+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- ffmpeg (system installation)
- tesseract-ocr (for OCR of scanned agenda PDFs)
- poppler (for pdf2image)
- Node.js 18+ (for frontend)
- OpenAI API key
- Anthropic API key (for v2 summary generation with Claude Sonnet)

### Install System Dependencies

```bash
# macOS
brew install ffmpeg tesseract poppler

# Ubuntu/Debian
sudo apt install ffmpeg tesseract-ocr poppler-utils
```

## Environment Variables

Create a `.env` file:

```bash
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
FIRST_CLIP_ID=6669  # Optional: starting clip for --auto mode
```

## Pipeline Commands

```bash
# Process single clip
uv run python main.py 6669

# Process range of clips (inclusive)
uv run python main.py 6669 6675

# Auto-process from last clip + 1 (or FIRST_CLIP_ID)
uv run python main.py --auto

# Auto-process with limit
uv run python main.py --auto --max 5

# Scrape Granicus and process all new clips
uv run python main.py --scrape --max 100

# Reprocess existing clips
uv run python main.py 6669 --force

# Generate search index for frontend
uv run python main.py --generate-index

# Add timestamps to existing transcripts (for clickable video seeking)
uv run python main.py --update-transcripts
uv run python main.py --update-transcripts --max 10  # Limit to 10 clips

# Skip keeping audio files (saves disk space)
uv run python main.py 6669 --no-audio

# Advanced options
uv run python main.py 6669 --output-dir /path/to/output
uv run python main.py 6669 --summary-model gpt-4o-mini   # Cheaper summaries
uv run python main.py 6669 --quiet                       # Reduce output
```

### Two-Pass Summary Generation (v2)

Summaries use a two-pass approach for higher quality:
- **Pass 1 (GPT-4o)**: Extracts structured facts (votes, dollar amounts, names, timestamps) into `extracted_facts.json`
- **Pass 2 (Claude Sonnet)**: Generates section-by-section narrative from extracted facts, with `[timestamp: MM:SS]` markers for video deep-linking

```bash
# Upgrade all existing clips to v2 summaries (resumable — skips already done)
uv run python main.py --upgrade-summaries --max 9999

# Test v2 on specific clips before committing (writes to separate test dir)
uv run python main.py 44 45 --test-summary
uv run python main.py --test-summary --max 3              # 3 most recent clips
uv run python main.py --test-summary --test-summary-dir ./my_test

# Clean up old v1 summary artifacts
uv run python main.py --clean-v1-summaries
```

The `--upgrade-summaries` command is resumable: if it crashes or runs out of API credits, re-run the same command and it picks up where it left off (skips clips that already have `extracted_facts.json`).

### Run in Background

For long-running jobs:

```bash
# Start processing in background
nohup uv run python main.py --scrape --max 9999 > pipeline.log 2>&1 &

# Monitor progress
tail -f pipeline.log

# Check state
cat meetings_output/state.json | python -m json.tool

# Stop processing
pkill -f "python main.py"
```

## Probe Available Clips

Discover all available clip IDs without downloading:

```bash
# Probe clips 1-7000
uv run python probe_clips.py 1 7000

# Resume from where you left off
uv run python probe_clips.py

# Run in background
nohup uv run python probe_clips.py 1 7000 > probe.log 2>&1 &

# Check progress
cat meetings_output/available_clips.json | head -5
```

Results saved to `meetings_output/available_clips.json`.

**Important:** Once `available_clips.json` exists, `--auto` mode will use it to only process valid clips, skipping non-existent clip IDs automatically.

## Frontend

The frontend renders structured extracted facts directly in an Overview tab (votes with pass/fail badges, financial items, agenda items with clickable timestamps), plus tabs for Transcript, Agenda, and Official Minutes.

### Development

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/data/` to `../meetings_output/` via symlink.

### Production Build

```bash
cd frontend
npm run build
npm run preview  # Test production build locally
```

### Deploy

```bash
# Build and deploy to S3 + invalidate CloudFront cache
CLOUDFRONT_DISTRIBUTION_ID=E1234567890 ./deploy.sh
```

### Basic Auth (Optional)

The deployed site can be protected with basic authentication via a CloudFront Function. To set it up:

1. Edit `cloudfront/basic-auth.js` and configure your own credentials
2. Go to **CloudFront > Functions** in the AWS Console
3. Create a new function, paste the contents of `cloudfront/basic-auth.js`
4. Publish the function
5. Associate it with your distribution's **Viewer request** event on the default behavior

## RAG Q&A System

Ask natural-language questions across the entire meeting archive and get AI-generated answers with citations and video timestamps.

The RAG system ingests 5 source types per clip for comprehensive retrieval:
- **Extracted facts** — structured votes, financial items, attendance, agenda items
- **Summaries** — narrative sections split on `## ` headers with timestamps
- **Official minutes** — ground-truth record, split by sections with overlap
- **Agendas** — pre-meeting record, split by sections with overlap
- **Transcripts** — topic-aware ~500-word chunks with silence gap detection

### Setup

```bash
# Install API dependencies (includes anthropic)
uv sync --extra api

# Build the vector index (one-time, embeds all clips into ChromaDB)
uv run python -m api.ingest --all

# Check stats
uv run python -m api.ingest --stats
```

### Running Locally

You need two processes running — the API server and the frontend dev server:

```bash
# Terminal 1: Start the API server
uv run uvicorn api.server:app --reload --port 8000

# Terminal 2: Start the frontend dev server
cd frontend && npm run dev
```

Then open **http://localhost:5173/ask** in your browser.

The Vite dev server proxies `/api/*` requests to the FastAPI backend on port 8000.

### CLI Query (no server needed)

```bash
uv run python -m api.query "What has the city done about short-term rentals?"
uv run python -m api.query "budget for parks" --body Council --after 2023-01-01
uv run python -m api.query "zoning changes" --model gpt-4o-mini  # cheaper
```

### Keeping the Index Updated

```bash
# Process new clips and auto-ingest into RAG
uv run python main.py --scrape --max 10 --rag

# Or ingest newly processed clips after the fact
uv run python -m api.ingest --new

# Rebuild the entire index from scratch
uv run python main.py --rebuild-rag
```

### RAG Costs

| Item | Cost |
|------|------|
| Embedding full corpus (~54K chunks) | ~$1-3 one-time |
| Embedding per new clip (~20 chunks) | ~$0.001 |
| Query with GPT-4o synthesis | ~$0.03 per query |
| Query with GPT-4o-mini | ~$0.005 per query |
| ChromaDB storage | Free (local) |

## Tests

```bash
# All Python tests (ingestion, query, server, integration, summary v2, RAG e2e)
uv sync --extra dev --extra api
uv run pytest tests/ -x -v

# Run specific test files
uv run pytest tests/test_rag_e2e.py -v     # End-to-end RAG pipeline
uv run pytest tests/test_summary_v2.py -v  # Two-pass summary generation

# Frontend tests (React component tests)
cd frontend && npm test
```

## Output Structure

Files are named with date prefix for easy sorting and identification:

```
meetings_output/
  state.json                              # Pipeline state (tracks progress)
  index.json                              # Search index for frontend
  available_clips.json                    # Probed clip IDs
  rag_state.json                          # RAG ingestion state
  chroma_db/                              # ChromaDB vector store
  clips/
    {clip_id}/
      {date}_{title}_audio.mp3            # Downloaded audio
      transcript_{date}_{title}_audio.txt # Whisper transcription (plain text)
      transcript_{date}_{title}_audio_segments.json # Timestamped segments
      summary.txt                         # AI-generated summary (v2: section-by-section)
      extracted_facts.json                # Structured extraction (votes, amounts, names)
      {date}_agenda_{title}.pdf           # Meeting agenda (if available)
      {date}_agenda_{title}.txt           # Extracted agenda text
      {date}_minutes_{title}.pdf          # Meeting minutes (if available)
      {date}_minutes_{title}.txt          # Extracted minutes text
      metadata.json                       # Clip metadata
```

## Architecture

CivicLens runs as a multi-tenant SaaS platform with three main components:

### API Server (FastAPI)

The core of the platform. Handles authentication, RAG Q&A, tenant management, billing, integrations, and serves meeting data. Each tenant (city/jurisdiction) gets isolated data, its own API key, and configurable Granicus connection.

```bash
# Run locally
uv run uvicorn api.server:app --reload --port 8000

# Or via Docker
docker build -t civiclens .
docker run -p 8000:8000 --env-file .env civiclens
```

### Frontend (React SPA)

Connects to the API server. In production, served by the same Docker container or deployed separately to a CDN. The Vite dev server proxies `/api/*` to the backend.

### Pipeline (`main.py`)

Processes meetings for a given tenant: downloads audio, transcribes, extracts structured facts, generates summaries, and ingests into the vector store. Can run on a schedule via the built-in scheduler or AWS Lambda.

```bash
# Process meetings for a specific tenant
uv run python main.py --tenant-id elk-grove-ca --scrape --max 10 --rag
```

### Deployment

The recommended production deployment is Docker behind a reverse proxy (or on AWS ECS/Fargate). See [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md) for full deployment instructions and [infra/](infra/) for Terraform configuration.


## API Costs

Per clip (approximate):
- Whisper: ~$0.006/min of audio
- GPT-4o extraction (v2 Pass 1): ~$0.07
- Claude Sonnet narration (v2 Pass 2): ~$0.08
- GPT-4o-mini: ~$0.001 per topic extraction

A typical 1-hour meeting costs ~$0.50-1.00 to process end-to-end, or ~$0.15 for summary upgrade only.

## License

MIT
