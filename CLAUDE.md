# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LFUCG Meeting Pipeline - Downloads, transcribes, and generates comprehensive summaries from Lexington-Fayette Urban County Government (LFUCG) city council meeting video clips hosted on Granicus. Includes a React SPA frontend for browsing and searching the meeting archive, plus a RAG-powered Q&A system for natural-language queries across the entire meeting archive.

## Reference Documentation

- **`granicus.md`** - Granicus API documentation including video player URL parameters (entrytime/stoptime for seeking), Legistar Web API, MediaManager SOAP API, RSS feeds, and embed options

## Commands

```bash
# Install dependencies (using uv)
uv sync

# Run the pipeline
uv run python main.py 6669                    # Process single clip
uv run python main.py 6669 6675               # Process range (inclusive)
uv run python main.py --auto                  # Auto-process from FIRST_CLIP_ID or last + 1
uv run python main.py --auto --max 5          # Auto-process with limit
uv run python main.py --scrape                # Scrape and process all new clips
uv run python main.py --scrape --max 100      # Scrape with limit
uv run python main.py --generate-index        # Generate search index from all clips
uv run python main.py 6669 --force            # Reprocess even if files exist
uv run python main.py 6669 --no-audio         # Don't keep audio files

# Probe available clips (without downloading)
uv run python probe_clips.py 1 7000           # Probe clip IDs 1-7000
uv run python probe_clips.py                  # Resume from last checked
# Once available_clips.json exists, --auto uses it to skip invalid clips

# Run in background
nohup uv run python main.py --scrape --max 9999 > pipeline.log 2>&1 &
tail -f pipeline.log                          # Monitor progress

# Update only minutes + summary (skip audio/transcription)
uv run python main.py 6669 --update-summary              # Single clip
uv run python main.py 6669 6680 --update-summary         # Range of clips

# Backfill missing minutes/agenda for already-processed clips
uv run python main.py --backfill-docs                    # Check all clips (highest first)
uv run python main.py --backfill-docs --max 50           # Check 50 most recent clips
uv run python main.py --backfill-docs --regenerate-summary  # Also regenerate summaries

# Add timestamps to existing transcripts (re-transcribe with Whisper)
uv run python main.py --update-transcripts               # All clips missing timestamps
uv run python main.py --update-transcripts --max 10      # Limit to 10 clips

# Advanced options
uv run python main.py 6669 --output-dir /path/to/output
uv run python main.py 6669 --summary-model gpt-4o-mini   # Cheaper summaries
uv run python main.py 6669 --quiet                       # Reduce output

# Two-pass summary generation (v2)
# Pass 1: GPT-4o extracts structured facts (votes, amounts, names, timestamps) into JSON
# Pass 2: Claude Sonnet generates section-by-section narrative from extracted facts
# Requires ANTHROPIC_API_KEY in .env
uv run python main.py --upgrade-summaries --max 9999     # Upgrade all clips (skips already done)
uv run python main.py --upgrade-summaries                # Upgrade 10 clips (default --max)
uv run python main.py 44 45 --test-summary               # Test v2 on specific clips (writes to test dir)
uv run python main.py --test-summary --max 3             # Test v2 on 3 most recent clips
uv run python main.py --test-summary --test-summary-dir ./my_test  # Custom test output dir

# Clean up old v1 summaries
uv run python main.py --clean-v1-summaries               # Remove summary.html everywhere + summary.txt from non-upgraded clips

# RAG Q&A system
uv sync --extra rag --extra dev                          # Install RAG + test dependencies (includes anthropic)
uv run python -m rag.ingest --all                        # Ingest all clips into vector store
uv run python -m rag.ingest --new                        # Ingest only new clips
uv run python -m rag.ingest --clip 6669                  # Ingest a specific clip
uv run python -m rag.ingest --stats                      # Show collection stats
uv run python main.py --rebuild-rag                      # Re-embed all clips from scratch
uv run python main.py 6669 --rag                         # Process clip + auto-ingest into RAG

# RAG query (CLI)
uv run python -m rag.query "What has the city done about short-term rentals?"
uv run python -m rag.query "budget for parks" --body Council --after 2023-01-01
uv run python -m rag.query "zoning changes" --model gpt-4o-mini

# RAG API server
uv run uvicorn rag.server:app --reload --port 8000

# Frontend development
cd frontend && npm install && npm run dev

# Build frontend for production
cd frontend && npm run build

# Run tests
uv run pytest tests/ -x -v                               # All Python tests
cd frontend && npm test                                   # Frontend tests
```

## Environment Variables

Set in `.env` file:
- `OPENAI_API_KEY` - OpenAI API key for transcription/summarization/embeddings
- `ANTHROPIC_API_KEY` - Anthropic API key for Claude Sonnet (v2 summary narration)
- `FIRST_CLIP_ID` - Starting clip ID for auto-processing (default: 6669)
- `LFUCG_OUTPUT_DIR` - Output directory for RAG server (default: ./lfucg_output)

## System Requirements

- Python 3.9+
- ffmpeg (system installation required)
- yt-dlp (installed via uv)
- tesseract-ocr (for OCR of scanned agenda PDFs)
- poppler (for pdf2image, required by OCR)
- Node.js 18+ (for frontend development)
- OpenAI API key
- Anthropic API key (for v2 summary generation)

## Architecture

### Backend Pipeline (`main.py`)

Single-file pipeline with `LFUCGPipeline` class that orchestrates:

1. **Title Extraction** - Uses yt-dlp to get original clip title
2. **Metadata Scraping** - Extracts date and meeting body from title
3. **Download** - Uses yt-dlp to download audio as MP3 (48kbps, 22kHz mono)
4. **Compression** - Compresses audio via ffmpeg if >24MB (Whisper API limit is 25MB)
5. **Transcription** - Uses OpenAI Whisper API with segment timestamps (new transcriptions get timestamped segments for video seeking)
6. **Agenda Download** - Downloads PDF agenda and extracts text with pdfplumber (falls back to OCR via pytesseract for scanned PDFs)
7. **Minutes Download** - Downloads official meeting minutes (PDF or HTML) if available and extracts text
8. **Topic Extraction** - Uses gpt-4o-mini to extract 3-8 topics
9. **Summary Generation** - See "Two-Pass Summary System" below
10. **Index Generation** - Creates searchable index.json for frontend

### Two-Pass Summary System (`summary_v2.py`)

Replaces the old single-pass GPT-4o summary with a two-pass approach:

- **Pass 1 (GPT-4o)**: Structured JSON extraction — votes with roll calls, financial items with dollar amounts, attendance, agenda items, public comments, appointments, contentious items. Saved as `extracted_facts.json` per clip. Uses `response_format=json_object` and temperature 0.1 for precision.
- **Pass 2 (Claude Sonnet)**: Section-by-section narrative from extracted facts. Only generates sections when data exists (no more "None discussed"). Each section is self-contained with `[timestamp: MM:SS]` markers for video deep-linking. ~100-400 words per section.

Key design decisions:
- Sections are conditional — skips empty sections, generates dynamic agenda item sections for significant items
- `extracted_facts.json` is the primary structured data source for the frontend Overview tab
- `summary.txt` (v2) is the narrative version, chunked by `## ` headers for RAG ingestion
- Cost: ~$0.15/clip (vs $0.06 for old single-pass). Negligible for incremental processing.

CLI workflow for upgrading existing clips:
```bash
uv run python main.py --upgrade-summaries --max 9999   # Run v2 on all clips (resumable)
uv run python main.py --clean-v1-summaries              # Remove old HTML + stale v1 summaries
uv run python main.py --rebuild-rag                     # Re-embed everything
```

### RAG Q&A System (`rag/`)

Natural-language Q&A over the meeting archive using retrieval-augmented generation:
- **`rag/ingest.py`** - Chunks clip outputs into 5 source types for embedding:
  - **summary** — narrative sections split on `## ` headers, with `[timestamp: MM:SS]` parsed into `start_time` metadata
  - **facts** — structured data from `extracted_facts.json` converted to searchable text (votes, financial items, attendance, agenda items, public comments, contentious items)
  - **minutes** — official minutes split by section boundaries with ~100-word overlap
  - **agenda** — agenda text split by section boundaries with ~100-word overlap
  - **transcript** — topic-aware chunks (~500 words) with silence gap and procedural phrase boundary detection, ~100-word overlap
- **`rag/query.py`** - Embeds question, retrieves top-K chunks from ChromaDB with metadata filtering, deduplicates (max 4 chunks/clip, max 2 per source type per clip for diversity), synthesizes answer via gpt-4o with citations
- **`rag/server.py`** - FastAPI with `POST /api/ask` and `GET /api/health` endpoints. Singleton OpenAI client, input validation (empty/length), error handling.
- **`rag/prompts.py`** - System prompts for LLM synthesis. Instructs `[Clip ID, MM:SS]` citation format, prefers facts and minutes for precise data.
- Vector store: ChromaDB (local, persisted to `lfucg_output/chroma_db/`)
- Embedding model: `text-embedding-3-small` (1536 dims)
- Supports metadata filters: meeting_body, date_after, date_before
- Incremental ingestion tracked via `lfucg_output/rag_state.json`

### Frontend (`frontend/`)

React 18 SPA with:
- Vite build system
- React Router for navigation
- FlexSearch for client-side full-text search (lazy-loaded, chunked index)
- Component-based architecture (MeetingList, MeetingDetail, SearchBar, TopicFilter, AskQuestion)
- **MeetingDetail** has tabbed view: Overview (extracted facts), Transcript (timestamped), Agenda, Official Minutes
- **Overview tab** renders structured `extracted_facts.json` directly — votes with pass/fail badges, financial items, agenda items, public comments, appointments, contested items. Timestamps are clickable (jump to video).
- RAG Q&A interface at `/ask` route with filter dropdowns, source cards, and Granicus video timestamp links

### AWS Lambda (`lambda/`)

Lambda handler for scheduled meeting sync:
- Triggered by EventBridge (12pm and 8pm weekdays)
- Processes new clips (configurable `max_clips`, default 5)
- Generates updated search index
- Syncs to S3 for frontend serving
- Requires `OPENAI_API_KEY`, optional `S3_BUCKET` env vars

## State Management

- Pipeline state persisted to `lfucg_output/state.json`
- Tracks last processed clip ID, processed clips list, and failed clips
- Supports resumption and incremental processing
- RAG ingestion state persisted to `lfucg_output/rag_state.json` (tracks which clips have been embedded)
- Summary upgrade is resumable: `--upgrade-summaries` skips clips that already have `extracted_facts.json`

## Output Structure

```
lfucg_output/
  state.json                              # Pipeline state
  index.json                              # Search index for frontend
  available_clips.json                    # Probed clip IDs (from probe_clips.py)
  rag_state.json                          # RAG ingestion state (which clips are embedded)
  chroma_db/                              # ChromaDB vector store
  clips/
    {clip_id}/
      {date}_{title}_audio.mp3            # Downloaded audio
      transcript_{date}_{title}_audio.txt # Whisper transcription (plain text)
      transcript_{date}_{title}_audio_segments.json # Timestamped segments (for video seeking)
      summary.txt                         # AI-generated summary (v2: section-by-section narrative)
      extracted_facts.json                # Structured extraction (votes, amounts, names, timestamps)
      {date}_agenda_{title}.pdf           # Meeting agenda PDF (if available)
      {date}_agenda_{title}.txt           # Extracted agenda text
      {date}_minutes_{title}.pdf          # Official meeting minutes PDF (if available)
      {date}_minutes_{title}.html         # Official meeting minutes HTML (if available)
      {date}_minutes_{title}.txt          # Extracted minutes text
      metadata.json                       # Processing metadata

summary_v2.py                             # Two-pass summary generation module

rag/
  __init__.py
  ingest.py                               # Chunking + embedding + ChromaDB storage
  query.py                                # Retrieval + LLM synthesis logic
  server.py                               # FastAPI endpoints
  prompts.py                              # System prompts for synthesis

tests/
  conftest.py                             # Shared fixtures: sample data, mocks, temp dirs
  test_ingest.py                          # Tests for chunking, embedding, ChromaDB storage
  test_query.py                           # Tests for retrieval, filtering, synthesis
  test_server.py                          # Tests for FastAPI endpoints (TestClient)
  test_integration.py                     # Tests for main.py pipeline hooks
  test_summary_v2.py                      # Tests for two-pass summary extraction + narration

frontend/
  dist/                                   # Built React SPA
  src/
    components/                           # React components
      MeetingDetail.jsx                   # Meeting view with Overview/Transcript/Agenda/Minutes tabs
      MeetingList.jsx                     # Browse/search meetings
      AskQuestion.jsx                     # RAG Q&A interface
      SearchBar.jsx                       # Search input
      TopicFilter.jsx                     # Filter/sort controls
      __tests__/
        AskQuestion.test.jsx              # Component tests (Vitest + React Testing Library)
    hooks/
      useMeetings.js                      # Data fetching (metadata, extracted facts, transcript, agenda, minutes)
      useSearch.js                        # Search and filter logic
      useFlexSearch.js                    # Full-text search engine (lazy-loaded)
    contexts/
      SearchContext.jsx                   # Global search state provider
  package.json
  vite.config.js

lambda/
  sync_meetings.py                        # Lambda handler
  requirements.txt                        # Lambda dependencies
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
    "audio": "2026-01-08_January_8_2026_WQFB_meeting_audio.mp3",
    "transcript": "transcript_2026-01-08_January_8_2026_WQFB_meeting_audio.txt",
    "transcript_segments": "transcript_2026-01-08_January_8_2026_WQFB_meeting_audio_segments.json",
    "summary_txt": "summary.txt",
    "extracted_facts": "extracted_facts.json",
    "agenda_pdf": "2026-01-08_agenda_January_8_2026_WQFB_meeting.pdf",
    "agenda_txt": "2026-01-08_agenda_January_8_2026_WQFB_meeting.txt",
    "minutes_pdf": "2026-01-08_minutes_January_8_2026_WQFB_meeting.pdf",
    "minutes_txt": "2026-01-08_minutes_January_8_2026_WQFB_meeting.txt"
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
  "attendance": {
    "present": ["Beasley", "Boone", "Brown"],
    "absent": [],
    "late": []
  },
  "motions_and_votes": [
    {
      "identifier": "Ordinance 0016-26",
      "description": "Zoning change from Agricultural-Rural to Medium Density Residential",
      "motion_by": "Brown",
      "second_by": "Curtis",
      "outcome": "passed",
      "vote_type": "roll_call",
      "ayes": 8,
      "nays": 0,
      "abstentions": 0,
      "votes_for": ["Beasley", "Boone"],
      "votes_against": [],
      "conditions": null,
      "transcript_approx_time": "25:15"
    }
  ],
  "financial_items": [
    {
      "description": "General Obligation Bonds",
      "amount": "$18,040,000",
      "type": "appropriation",
      "identifier": null,
      "vendor_or_recipient": null
    }
  ],
  "public_comments": [
    {
      "speaker": "John Smith",
      "topic": "Zoning concerns",
      "summary": "Expressed concerns about increased traffic from rezoning.",
      "transcript_approx_time": "15:30"
    }
  ],
  "agenda_items": [
    {
      "identifier": "Ordinance 0016-26",
      "title": "Zoning Change - Agricultural to Residential",
      "type": "ordinance",
      "summary": "Changed zone from Agricultural-Rural to Medium Density Residential.",
      "key_speakers": ["Brown"],
      "outcome": "approved",
      "transcript_approx_time": "25:15"
    }
  ],
  "appointments": [],
  "contentious_items": []
}
```
