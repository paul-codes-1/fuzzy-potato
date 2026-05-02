# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LFUCG Meeting Pipeline - Downloads, transcribes, and generates comprehensive summaries from Lexington-Fayette Urban County Government (LFUCG) city council meeting video clips hosted on Granicus. Includes a React SPA frontend for browsing and searching the meeting archive, plus a RAG-powered Q&A system for natural-language queries across the entire meeting archive.

## Reference Documentation

- **`granicus.md`** - Granicus API documentation including video player URL parameters (entrytime/stoptime for seeking), Legistar Web API, MediaManager SOAP API, RSS feeds, embed options, and the WebVTT closed-captioning track
- **`granicus_captions.py`** - WebVTT parsing, speaker turn coalescing, max-overlap alignment onto Whisper segments, and VTT-as-placeholder transcript rendering

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
uv run python main.py --generate-index        # Generate index.json + search.db from all clips
uv run python main.py --build-search-db       # Rebuild only the SQLite FTS5 search.db (~30s)
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

# Granicus closed-caption (VTT) backfill
# Speaker enrichment for clips that have Whisper segments + VTT
# OR VTT-as-placeholder transcript for clips that haven't been Whispered yet
uv run python scripts/backfill_captions.py --dry-run     # Inventory what would change
uv run python scripts/backfill_captions.py --workers 6   # Run with 6 parallel yt-dlp workers
uv run python scripts/backfill_captions.py --clip 6757   # Single clip
uv run python scripts/backfill_captions.py --force       # Re-fetch + re-parse (after parser fixes)

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
5b. **Closed-caption ingestion** - Pulls Granicus WebVTT (live-CC stenographer track) via yt-dlp. Available on ~50% of clips. Used to attach speaker labels to Whisper segments via timestamp max-overlap, OR to synthesize a placeholder transcript when Whisper hasn't run yet. See "Granicus Captions" below.
6. **Agenda Download** - Downloads PDF agenda and extracts text with pdfplumber (falls back to OCR via pytesseract for scanned PDFs)
7. **Minutes Download** - Downloads official meeting minutes (PDF or HTML) if available and extracts text
8. **Topic Extraction** - Uses gpt-4o-mini to extract 3-8 topics
9. **Summary Generation** - See "Two-Pass Summary System" below
10. **Index Generation** - Creates searchable index.json for frontend

### Granicus Captions (`granicus_captions.py`)

Two-mode integration with Granicus's live-CC WebVTT track:

- **Speaker enrichment** — for clips that already have Whisper segments, parse VTT into speaker turns (`>> Mayor Gorton:` / `>> councilmember hale:` markers) and align speakers onto Whisper segments by timestamp max-overlap. The Whisper text remains canonical; only attribution is added. `transcript_source` becomes `"whisper-1+vtt-speakers"`.
- **VTT-as-placeholder** — for un-Whispered clips, render the VTT directly as a transcript so the meeting page is searchable / RAG-ingestible immediately. The page disclosure says "Closed-caption placeholder — Whisper transcription pending." `transcript_source` becomes `"granicus_vtt"`. Replaced when Whisper runs.

VTT URL discovery uses yt-dlp (`--write-subs --sub-langs en --skip-download`). Result is cached in `lfucg_output/clips/<id>/captions.vtt`.

Parser hardened against four real-world VTT quirks observed during the full-archive backfill:
- **Stenographer interjection mis-attribution** — stop-list rejects `>> thank you:` etc. as speaker names.
- **Rolling-caption duplication** — Granicus live-CC repeats the steno's buffer window; consecutive duplicate lines within a cue are collapsed.
- **ASCII control-byte corruption** — older clips have `\x7f` (DEL) runs from malformed encoders; control bytes are stripped before further processing.
- **Stenographer keystroke noise** — lines like `ww ww www` lack a 3+ letter word with 2+ distinct letters, so they're dropped.

Frontend renders speaker labels in the Transcript tab and varies the AI-disclosure aside by `transcript_source`. The `clip.md` Markdown alternate emits a "Speakers:" line and a source-specific disclosure paragraph.

RAG ingestion prefixes speaker changes (`Mayor Gorton: ...`) into transcript chunk text so attribution influences retrieval, and writes `transcript_source` + comma-joined `speakers` into ChromaDB metadata.

One-shot backfill: `scripts/backfill_captions.py` (idempotent, parallel via `--workers N`, supports `--dry-run` / `--clip <id>` / `--force`). Skips clips that already have the target state.

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
  - **transcript** — topic-aware chunks (~500 words) with silence gap and procedural phrase boundary detection, ~100-word overlap. When per-segment speakers exist, speaker changes are prefixed (`Mayor Gorton: ...`) into the embedded text, and `transcript_source` + comma-joined `speakers` are written to ChromaDB metadata.
- **`rag/query.py`** - Embeds question, retrieves top-K chunks from ChromaDB with metadata filtering, deduplicates (max 4 chunks/clip, max 2 per source type per clip for diversity), synthesizes answer via gpt-4o with citations
- **`rag/server.py`** - FastAPI with `POST /api/ask` and `GET /api/health` endpoints. Singleton OpenAI client, input validation (empty/length), error handling.
- **`rag/prompts.py`** - System prompts for LLM synthesis. Instructs `[Clip ID, MM:SS]` citation format, prefers facts and minutes for precise data.
- Vector store: ChromaDB (local, persisted to `lfucg_output/chroma_db/`)
- Embedding model: `text-embedding-3-small` (1536 dims)
- Supports metadata filters: meeting_body, date_after, date_before
- Incremental ingestion tracked via `lfucg_output/rag_state.json`

### Server-Side Search (`rag/search.py`, `scripts/build_search_db.py`)

Replaces the old client-side FlexSearch (40 MB chunked JSON downloaded + indexed on every browser cold-mount). The full-text index now lives server-side in a SQLite FTS5 database (`lfucg_output/search.db`, ~300 MB) and is queried via the App Runner RAG container:

- **`scripts/build_search_db.py`** — destructive rebuilder. One row per clip with title/transcript/agenda/minutes/facts as searchable columns; speakers/body/date as UNINDEXED filter columns. ~30s on the full archive. Auto-rebuilt by `pipeline.generate_search_index()` and at the end of every `--auto`/`--scrape` batch (per-clip skips the FTS rebuild via `build_search_db=False` to avoid 30s × N).
- **`rag/search.py`** — BM25-ranked search + filters + snippets. Title weighted 10×, facts 5×, speakers 3×, agenda 2×, minutes 1.5×, transcript 1×. Snippets HTML-escaped server-side; sentinel marks (`\x01M\x01`) are reinserted as `<mark>` so the frontend can render via `dangerouslySetInnerHTML` without XSS risk.
- **`rag/related.py`** — "more like this" using the centroid of the source clip's existing summary embeddings. Reuses ChromaDB; no extra OpenAI calls.
- **Endpoints (rag/server.py)**:
  - `POST /api/search` — `{q, meeting_body?, speaker?, date_after?, date_before?, limit?}` → ranked clips + pre-marked snippets
  - `GET  /api/suggest?q=...` — autocomplete over titles/bodies/topics/speakers
  - `GET  /api/facets` — populates filter dropdowns (bodies, top-30 speakers by count, date_min/max)
  - `GET  /api/related/{clip_id}` — top-5 similar clips with similarity scores

The `search.db` file is baked into the App Runner Docker image (Dockerfile + `.dockerignore` whitelist). `tests/test_search.py` and `tests/test_server_search.py` cover the builder + endpoint surfaces.

### Frontend (`frontend/`)

React 18 SPA with:
- Vite build system
- React Router for navigation
- Server-backed full-text search via `useServerSearch` (POST /api/search, debounced 250ms, AbortController on cancel). No client-side index — the homepage loads instantly.
- Autocomplete dropdown via `useSuggestions` (GET /api/suggest, 100ms debounce, ↑/↓/Enter/Esc keyboard navigation)
- Speaker filter dropdown sourced from `useFacets` (GET /api/facets, cached at module level — single fetch on mount)
- "Related meetings" section on the detail page via `useRelatedClips` (GET /api/related/{id})
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
  index.json                              # Metadata index (browse list, filters, sort) — 3.8 MB
  search.db                               # SQLite FTS5 full-text index (powers /api/search) — ~300 MB
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
      captions.vtt                        # Raw Granicus closed-captioning (if available)
      metadata.json                       # Processing metadata

granicus_captions.py                      # WebVTT parsing + speaker alignment module
summary_v2.py                             # Two-pass summary generation module

rag/
  __init__.py
  ingest.py                               # Chunking + embedding + ChromaDB storage
  query.py                                # Retrieval + LLM synthesis logic
  server.py                               # FastAPI endpoints (/ask, /chat, /search, /suggest, /facets, /related)
  search.py                               # SQLite FTS5 BM25 search + suggest + facets
  related.py                              # ChromaDB-backed "more like this" lookup
  prompts.py                              # System prompts for synthesis

tests/
  conftest.py                             # Shared fixtures: sample data, mocks, temp dirs
  test_ingest.py                          # Tests for chunking, embedding, ChromaDB storage
  test_query.py                           # Tests for retrieval, filtering, synthesis
  test_server.py                          # Tests for /api/ask, /api/chat (TestClient)
  test_server_search.py                   # Tests for /api/search, /api/suggest, /api/facets, /api/related
  test_search.py                          # Tests for SQLite FTS5 builder + ranking + filters + snippets
  test_integration.py                     # Tests for main.py pipeline hooks
  test_summary_v2.py                      # Tests for two-pass summary extraction + narration
  test_captions.py                        # Tests for VTT parsing, speaker alignment, garbage-filter

scripts/
  build_search_db.py                      # SQLite FTS5 index builder (rebuilds search.db from clips/)
  backfill_captions.py                    # One-shot VTT backfill: speaker enrichment + placeholder transcripts
  restamp-cache-headers.sh                # S3 cache-control header retrofit

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
      useSearch.js                        # Search/filter/sort orchestration (URL-param state)
      useServerSearch.js                  # POST /api/search with debounce + AbortController
      useSuggestions.js                   # GET /api/suggest for autocomplete dropdown
      useFacets.js                        # GET /api/facets — module-level cached
      useRelatedClips.js                  # GET /api/related/{clip_id} — for the detail page
      __tests__/
        useServerSearch.test.js           # Hook test: debounce, filters, error handling
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
    "minutes_txt": "2026-01-08_minutes_January_8_2026_WQFB_meeting.txt",
    "captions_vtt": "captions.vtt"
  },
  "processed_at": "...",
  "processing_time_seconds": 120.5,
  "transcript_words": 6660,
  "audio_kept": true,
  "transcript_source": "whisper-1+vtt-speakers",
  "speakers": ["Mayor Gorton", "Councilmember Hale"],
  "models": {
    "transcribe": "whisper-1",
    "summary": "gpt-4o+claude-sonnet",
    "topics": "gpt-4o-mini"
  }
}
```

`transcript_source` is one of:
- `"whisper-1"` — Whisper transcript only, no captions track available
- `"whisper-1+vtt-speakers"` — Whisper transcript + speaker labels folded in from VTT
- `"granicus_vtt"` — Placeholder transcript synthesized from VTT (Whisper hasn't run yet)

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
