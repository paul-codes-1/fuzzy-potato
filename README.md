# LFUCG Meeting Pipeline

Downloads, transcribes, and generates comprehensive summaries from Lexington-Fayette Urban County Government (LFUCG) city council meeting video clips hosted on Granicus. Includes a React SPA frontend for browsing and searching the meeting archive, plus a RAG-powered Q&A system for natural-language queries across the entire meeting archive.

## Documentation

- **[granicus.md](granicus.md)** - Granicus platform documentation including:
  - Video player URL parameters (`entrytime`, `stoptime` for timestamp linking)
  - Legistar Web API (REST API for legislative data)
  - MediaManager SOAP API (video management)
  - RSS feeds for agendas/minutes
  - Embed options and JavaScript player API

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

### Granicus Closed-Caption Backfill

Granicus serves a live-CC WebVTT track for ~50% of clips. The pipeline pulls it on every new clip (`process_clip` automatically), but for the existing archive there's a one-shot backfill that does two things:

1. **Speaker enrichment** — for clips that already have a Whisper transcript, fold `>> Speaker:` attributions from VTT onto each Whisper segment by timestamp max-overlap
2. **VTT-as-placeholder** — for un-Whispered clips, synthesize a transcript directly from the VTT so the meeting page is searchable until Whisper runs

```bash
# Inventory what would change (no network calls)
uv run python scripts/backfill_captions.py --dry-run

# Run for real with 6 parallel workers (~30 min for full archive)
uv run python scripts/backfill_captions.py --workers 6

# Specific clip / re-fetch after parser fixes
uv run python scripts/backfill_captions.py --clip 6757
uv run python scripts/backfill_captions.py --force --workers 6
```

The script is idempotent — clips that are already in the target state are skipped.

### Run in Background

For long-running jobs:

```bash
# Start processing in background
nohup uv run python main.py --scrape --max 9999 > pipeline.log 2>&1 &

# Monitor progress
tail -f pipeline.log

# Check state
cat lfucg_output/state.json | python -m json.tool

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
cat lfucg_output/available_clips.json | head -5
```

Results saved to `lfucg_output/available_clips.json`.

**Important:** Once `available_clips.json` exists, `--auto` mode will use it to only process valid clips, skipping non-existent clip IDs automatically.

## Frontend

The frontend renders structured extracted facts directly in an Overview tab (votes with pass/fail badges, financial items, agenda items with clickable timestamps), plus tabs for Transcript, Agenda, and Official Minutes.

### Development

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/data/` to `../lfucg_output/` via symlink.

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

### Basic Auth

The deployed site is protected with basic authentication:

- **Username:** `public`
- **Password:** `L3tMe1n!`

This is implemented as a CloudFront Function (`cloudfront/basic-auth.js`). To set it up:

1. Go to **CloudFront > Functions** in the AWS Console
2. Create a new function, paste the contents of `cloudfront/basic-auth.js`
3. Publish the function
4. Associate it with your distribution's **Viewer request** event on the default behavior

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
# Install RAG dependencies (includes anthropic)
uv sync --extra rag

# Build the vector index (one-time, embeds all clips into ChromaDB)
uv run python -m rag.ingest --all

# Check stats
uv run python -m rag.ingest --stats
```

### Running Locally

You need two processes running — the API server and the frontend dev server:

```bash
# Terminal 1: Start the RAG API server
uv run uvicorn rag.server:app --reload --port 8000

# Terminal 2: Start the frontend dev server
cd frontend && npm run dev
```

Then open **http://localhost:5173/ask** in your browser.

The Vite dev server proxies `/api/*` requests to the FastAPI backend on port 8000.

### CLI Query (no server needed)

```bash
uv run python -m rag.query "What has the city done about short-term rentals?"
uv run python -m rag.query "budget for parks" --body Council --after 2023-01-01
uv run python -m rag.query "zoning changes" --model gpt-4o-mini  # cheaper
```

### Keeping the Index Updated

```bash
# Process new clips and auto-ingest into RAG
uv run python main.py --scrape --max 10 --rag

# Or ingest newly processed clips after the fact
uv run python -m rag.ingest --new

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
uv sync --extra dev --extra rag
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
lfucg_output/
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
      captions.vtt                        # Granicus closed-caption track (if available)
      metadata.json                       # Clip metadata
```

`metadata.json` carries a `transcript_source` field — one of `whisper-1`, `whisper-1+vtt-speakers` (Whisper + Granicus speaker labels), or `granicus_vtt` (placeholder transcript synthesized from VTT until Whisper runs). The `speakers` field lists distinct attributed speakers when known.

## Architecture

Your frontend already fetches everything from relative /data/ paths. You have two main options:

  Option 1: S3 + CloudFront (recommended)

  1. Upload lfucg_output/ to S3, mapping it to a /data/ prefix:

  ***
`  aws s3 sync lfucg_output/ s3://public-meetings/data/ --exclude "state.json" --exclude "*.mp3"
`
***

  2. Deploy the built frontend (frontend/dist/) to the same bucket root:

      `cd frontend && npm run build`
      
      `aws s3 sync dist/ s3://public-meetings/ --exclude "data/*"`
***

  3. Create a CloudFront distribution pointing to the bucket. The structure would be:
  s3://your-bucket/
    index.html          <- from frontend/dist/
    assets/             <- from frontend/dist/
    data/
      index.json        <- from lfucg_output/index.json
      clips/
        6669/
          metadata.json
          extracted_facts.json
          summary.txt
          ...
  4. Enable S3 static website hosting or use CloudFront with an OAC. For SPA routing, set up a custom error response that returns index.html for 403/404 errors (so React Router works).

  Option 2: Separate S3 origin (CORS)

  If you want the frontend hosted separately (e.g., Vercel/Netlify) and data on S3:

  1. Upload data to S3 and put CloudFront in front of it
  2. Add CORS headers on the S3 bucket/CloudFront
  3. Change the base URL in useMeetings.js to point to your CloudFront domain:
  const DATA_BASE = import.meta.env.VITE_DATA_URL || '/data'
  const INDEX_URL = `${DATA_BASE}/index.json`
  3. Then prefix all fetch calls with DATA_BASE instead of hardcoded /data.
  4. Set VITE_DATA_URL=https://d1234.cloudfront.net/data at build time.

  Option 1 is simpler since everything lives under one domain — no CORS, no env vars, and the code works as-is with zero changes. You just need to get the S3 directory structure to match what the fetches expect
  (/data/index.json, /data/clips/{id}/...).


## API Costs

Per clip (approximate):
- Whisper: ~$0.006/min of audio
- GPT-4o extraction (v2 Pass 1): ~$0.07
- Claude Sonnet narration (v2 Pass 2): ~$0.08
- GPT-4o-mini: ~$0.001 per topic extraction

A typical 1-hour meeting costs ~$0.50-1.00 to process end-to-end, or ~$0.15 for summary upgrade only.

## License

MIT
