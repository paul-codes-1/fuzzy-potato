# CLAUDE.md

> **System context:** This repo is one of four under `~/lt/` that together form The Lexington Times. See `~/lt/CLAUDE.md` for the cross-repo guide and `~/lt/.codebase-info/fuzzy-potato/overview.md` for the comprehensive technical overview of the LFUCG meeting pipeline + RAG.

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

# Table of Motions backfill (official motions from agenda packets)
# Work sessions have no minutes; the official motion record is the "Table of
# Motions" printed in the NEXT session's agenda packet. This scans agendas,
# extracts those tables, and REPLACES the referenced prior clip's
# transcript-derived motions with the official ones (re-ingests RAG +
# rebuilds search.db for touched clips).
uv run python main.py --backfill-tables-of-motions           # All agendas (newest first)
uv run python main.py --backfill-tables-of-motions --max 50  # Limit to 50 newest clips
uv run python main.py --backfill-tables-of-motions --force   # Re-apply (after parser fixes)
uv run python main.py --backfill-tables-of-motions --no-reingest  # Skip RAG/search rebuild

# Add timestamps to existing transcripts (re-transcribe with Whisper)
uv run python main.py --update-transcripts               # All clips missing timestamps
uv run python main.py --update-transcripts --max 10      # Limit to 10 clips

# Whisper the Council / Work Session / Committee of the Whole / Planning Commission
# clips that are still serving the Granicus caption track as their transcript
# (new clips for these bodies are Whispered inline by process_clip since 2026-09-12;
# this backfills the older placeholders). Newest first; ~$0.006/audio-min (whisper-1)
# + ~$0.12/clip for facts+summary; re-ingests + rebuilds the index at the end.
uv run python main.py --retranscribe-placeholders --dry-run            # census + cost estimate
uv run python main.py --retranscribe-placeholders --since 2026-01-01 --limit 20 --no-audio
uv run python main.py --retranscribe-placeholders --bodies "planning commission" --limit 5

# Re-open the retry budget for specific failed clips (next --auto + weekly sweep re-attempt them)
uv run python main.py --retry-failed 6804 6816 6825 6828 6832

# Pre-rendered per-clip HTML pages (SEO) — also runs at the end of every batch/--generate-index
uv run python main.py --prerender                        # incremental (fingerprint state)
uv run python main.py --prerender --full                 # after a new SPA bundle ships

# Advanced options
uv run python main.py 6669 --output-dir /path/to/output
uv run python main.py 6669 --summary-model gpt-4o-mini   # Cheaper summaries
uv run python main.py 6669 --quiet                       # Reduce output

# Two-pass summary generation (v2)
# Pass 1: GPT-4o extracts structured facts (votes, amounts, names, timestamps) into JSON
# Pass 2: Claude (Haiku 4.5 by default; LFUCG_NARRATION_MODEL to override) generates section-by-section narrative from extracted facts
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

# Local Whisper backfill (Mac-only) — free transcription for clips with NO transcript
# Runs on Apple Silicon via `mlx_whisper` (`uv tool install mlx-whisper`), ~60x realtime
# on an M4 Max. Targets ONLY clips with no files.transcript and no transcript_source
# (943 as of 2026-07); VTT-placeholder clips are left alone. Stages pipeline-identical
# artifacts locally, then pushes to the box + runs the standard finalize chain
# (rag.ingest --clip, --generate-index, /admin/reload, sync_data_s3.sh, CF invalidation).
# Backfilled clips get transcript_source="whisper-large-v3-local" (seo.py + MeetingDetail.jsx
# carry the matching disclosure). QA gate rejects Whisper repetition-loops before staging.
# ⚠️ Pushed clips lacking extracted_facts.json get facts+summary that night via
# summaries_cron (--upgrade-summaries) at ~$0.10-0.15/clip — size push batches accordingly.
python3 scripts/local_whisper_backfill.py census         # Build backlog.json from the box
python3 scripts/local_whisper_backfill.py run --max 10   # Download + transcribe + QA + stage
python3 scripts/local_whisper_backfill.py push           # rsync staged clips + finalize on box
python3 scripts/local_whisper_backfill.py status         # Progress + failures
# State in ~/lt/.whisper-backfill/lfucg/ — fully resumable; --retry-failed to retry failures.

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

# RAG API server (also serves the MCP endpoint at /api/mcp and /mcp)
uv run uvicorn rag.server:app --reload --port 8000

# Smoke-test the MCP endpoint locally (initialize handshake)
curl -X POST http://localhost:8000/api/mcp/ \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}'

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
- `ANTHROPIC_API_KEY` - Anthropic API key for Claude (v2 summary narration)
- `LFUCG_NARRATION_MODEL` - Claude model for Pass-2 narration (default: `claude-haiku-4-5`)
- `LFUCG_ANTHROPIC_MODEL` - Claude model for the opt-in RAG chat provider (default: `claude-sonnet-4-6`)
- `FIRST_CLIP_ID` - Starting clip ID for auto-processing (default: 6669)
- `LFUCG_OUTPUT_DIR` - Output directory for RAG server (default: ./lfucg_output)
- `VECTOR_BACKEND` - RAG vector store backend: `chroma` (default, in-RAM HNSW under `lfucg_output/chroma_db/`) or `sqlite` (disk-first sqlite-vec at `lfucg_output/vec.db`; see `rag/vecstore.py` + RAG_CAPACITY_PLAN.md). Flip together with `RAG_EMBED_DIMS` to match how vec.db was built.
- `RAG_EMBED_DIMS` - text-embedding-3-small dimensions for ingest AND query embeds (default 1536; the sqlite capacity-plan rebuild uses 512). Must match the serving store's dims.
- `LFUCG_SITE_URL` - Public site URL used in seo.py and the MCP server's URL decoration (default: `https://meetings.lexingtonky.news`)
- `RAG_MAX_DISTANCE` - cosine-distance gate for retrieved chunks (default **0.55**, calibrated 2026-09-12 on the 512-dim prod store; a 1536-dim Chroma dev store sits lower on the scale — set 0.75 there if the no-coverage rate spikes)
- `RAG_RECENCY_HALF_LIFE_DAYS` / `RAG_RECENCY_GRACE_DAYS` / `RAG_RECENCY_FLOOR` - date-aware re-rank of retrieved chunks (defaults 6400 / 365 / 0.5 → last 12 months untouched, ≈0.7× at 10 years; `0` half-life disables). `RAG_RECENCY_RESERVED_SLOTS` (3) / `RAG_RECENCY_RECENT_DAYS` (180) hold context slots for the newest material.
- `LFUCG_WHISPER_REQUIRED_BODIES` - regex of meeting titles/bodies whose Granicus VTT is only a placeholder (default `urban county council|council work session|committee of the whole|planning commission`; empty string disables)
- `PRERENDER_ENABLED` (`1`) / `PRERENDER_TEMPLATE` (path) - per-clip HTML pre-rendering kill switch + explicit SPA-shell template (default: fetch the live `<site_url>/index.html`, then `frontend/dist/index.html`)

## System Requirements

- Python 3.10+ (the `mcp` SDK requires 3.10+; the production Lightsail box runs 3.10.12. The App-Runner-era `Dockerfile`/`entrypoint.sh`/`ingest_all.sh`/`lambda/` and the disabled `.github/workflows/ingest.yml` second-ingest workflow were **deleted 2026-08-08** — production is the Lightsail git-checkout path only)
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
- **Whisper-required bodies (2026-09-12)** — `requires_whisper(title, body)` in `main.py` (regex `LFUCG_WHISPER_REQUIRED_BODIES`: Urban County Council, Council Work Session, Committee of the Whole, Planning Commission) makes `process_clip` treat a validated VTT as a placeholder ONLY: it downloads audio + Whispers in the same pass and folds the VTT back in for speaker labels (`whisper-1+vtt-speakers`). If the Whisper pass fails (download, empty, QA-rejected) the VTT placeholder is written so the page goes live and metadata gets `whisper_pending: true`; `--retranscribe-placeholders` (`find_placeholder_clips` → `retranscribe_clip` → `upgrade_clip_summary_v2` → re-ingest + index) backfills those and the historical placeholders. `retranscribe_clip` backs the placeholder up and restores it if Whisper output fails `validate_transcript`. Other bodies keep the cheap VTT shortcut.

VTT URL discovery uses yt-dlp (`--write-subs --sub-langs en --skip-download`). Result is cached in `lfucg_output/clips/<id>/captions.vtt`.

Parser hardened against four real-world VTT quirks observed during the full-archive backfill:
- **Stenographer interjection mis-attribution** — stop-list rejects `>> thank you:` etc. as speaker names.
- **Rolling-caption duplication** — Granicus live-CC repeats the steno's buffer window; consecutive duplicate lines within a cue are collapsed.
- **ASCII control-byte corruption** — older clips have `\x7f` (DEL) runs from malformed encoders; control bytes are stripped before further processing.
- **Stenographer keystroke noise** — lines like `ww ww www` lack a 3+ letter word with 2+ distinct letters, so they're dropped.

Frontend renders speaker labels in the Transcript tab and varies the AI-disclosure aside by `transcript_source`. The `clip.md` Markdown alternate emits a "Speakers:" line and a source-specific disclosure paragraph.

RAG ingestion prefixes speaker changes (`Mayor Gorton: ...`) into transcript chunk text so attribution influences retrieval, and writes `transcript_source` + comma-joined `speakers` into ChromaDB metadata.

One-shot backfill: `scripts/backfill_captions.py` (idempotent, parallel via `--workers N`, supports `--dry-run` / `--clip <id>` / `--force`). Skips clips that already have the target state.

### Table of Motions (`table_of_motions.py`)

LFUCG Council **work sessions produce no official minutes**. The authoritative structured record of a work session's motions is the **"Table of Motions"** — and it's printed in the *next* session's agenda packet (under agenda section III, "Approval of Summary"). So meeting N's official motion record lives inside meeting N+1's agenda PDF, as a self-contained block:

```
1                              <- packet page number
URBAN COUNTY COUNCIL           <- body header
WORK SESSION
TABLE OF MOTIONS               <- marker
May 12, 2026                   <- date of the meeting being recorded
Mayor Gorton called ... were present.   <- attendance preamble
II. Requested Rezonings/Docket Approval
    Motion by Ellinger to approve the May 14, 2026 Docket. Seconded by Wu. Motion passed without dissent.
    Motion by Sheehan ... Seconded by Lynch, the motion passed with a 11 - 4 vote (yes: ...; no: ...).
...
```

The module:
- **`extract_tables(agenda_text)`** — slices every block out of an agenda's extracted text (a packet can embed more than one), capturing body header, meeting date, attendance preamble, and per-section motions. Block ends where packet items resume (a `NNNN-NN` file number / "MAYOR LINDA GORTON" / "BUDGET AMENDMENT REQUEST LIST"). Parser hardening mirrors `granicus_captions.py`: ASCII control bytes stripped, bare page-number lines dropped before reassembling hard-wrapped sentences. Tolerant of OCR quirks (e.g. "witho0ut dissent" still reads unanimous) and ordinal dates ("October 15Th, 2013").
- **`parse_motion_line`** — handles every observed outcome form: `passed without dissent`, `(as amended)`, `passed 10-5 (yes: …; no: …)`, `passed with a 11 - 4 vote (…)`, `failed`/`defeated`, `tabled`. Maps onto the `motions_and_votes` schema. When a printed tally and its name list disagree (the source is sometimes inconsistent), the **tally is authoritative** for ayes/nays and names are stored as printed.
- **`resolve_target_clip(table, index_clips)`** — matches on **date + body class** (Council work session ≠ Planning Commission work session), keying on date + title-substring since ~2,100 older clips have `meeting_body=null`. **Aborts on ambiguous (>1) matches** (duplicate Granicus uploads — ~17 dates) rather than guessing.
- **`merge_table_into_facts`** — **replaces** the target clip's `motions_and_votes` with the official set (decision: official wins). Stamps `motions_source: "table_of_motions"` + `table_of_motions_ref: {source_clip_id, source_page, meeting_date}`. Because "replace" drops the Whisper motions (the only ones carrying `transcript_approx_time`), it does a free **best-effort timestamp carry-over**: each official motion inherits the video timestamp of the best description-aligned displaced motion, so the Overview tab's clickable video deep-links survive. Fills `attendance.present` from the preamble only when empty (never clobbers).

Pipeline integration (`main.py`):
- **Per-clip hook** — after `download_agenda`, `apply_tables_from_agenda` opportunistically applies any embedded table to the prior clip it records (re-ingests that clip into RAG when ingestion is enabled). Self-maintaining going forward.
- **Backfill** — `--backfill-tables-of-motions` sweeps all agendas (newest first), idempotent (skips clips already carrying `motions_source == "table_of_motions"` unless `--force`), then re-ingests touched clips into ChromaDB and rebuilds `search.db` once at the end (skip via `--no-reingest`). On the full local archive: ~277 tables, 224 uniquely matched, ~3,200 motions, 60 roll-calls; the rest are legitimately-absent meetings or ambiguity-guarded duplicate uploads.

`tests/test_table_of_motions.py` covers parsing variants, block-boundary detection, clip resolution + ambiguity guard, and the replace-merge with timestamp carry-over.

### Two-Pass Summary System (`summary_v2.py`)

Replaces the old single-pass GPT-4o summary with a two-pass approach:

- **Pass 1 (GPT-4o)**: Structured JSON extraction — votes with roll calls, financial items with dollar amounts, attendance, agenda items, public comments, appointments, contentious items. Saved as `extracted_facts.json` per clip. Uses `response_format=json_object` and temperature 0.1 for precision.
- **Pass 2 (Claude — Haiku 4.5 default, `LFUCG_NARRATION_MODEL` override)**: Section-by-section narrative from extracted facts. Only generates sections when data exists (no more "None discussed"). Each section is self-contained with `[timestamp: MM:SS]` markers for video deep-linking. ~100-400 words per section.

Key design decisions:
- Sections are conditional — skips empty sections, generates dynamic agenda item sections for significant items
- `extracted_facts.json` is the primary structured data source for the frontend Overview tab
- `summary.txt` (v2) is the narrative version, chunked by `## ` headers for RAG ingestion
- Cost: ~$0.15/clip on the original gpt-4o+Sonnet pairing; narration moved to Haiku 4.5 (2026-07) cutting the Claude share ~3x. Negligible for incremental processing.

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
- **`rag/query.py`** - Embeds question, retrieves top-K chunks from the vector store with metadata filtering, re-ranks by date, deduplicates (max **3** chunks/clip, max 2 per source type per clip for diversity), synthesizes answer via gpt-4o with citations. Retrieval-quality changes (2026-09-12 — retrieval was date-blind: a snow-plan question cited a 2015 clip over the Aug 2026 report):
  - **Recency re-rank** (`recency_rank_scores`) — each hit's similarity is multiplied by `recency_weight(date)` (1.0 inside a 365-day grace window, then an exponential decay with a 6400-day half-life ≈0.7 at 10 years, floor 0.5) BEFORE the per-clip dedup and the top-k cap, so a newer-but-slightly-less-similar chunk wins ties. Skipped when the question names a 4-digit year (`question_names_year`) or the caller passed date filters. `apply_reserved_recent_slots` then guarantees up to 3 of the 15 context slots to chunks from the last 180 days when any exist. All env-tunable (see Environment Variables).
  - `verify_citations` parses EVERY ID in a bracket (`[Clip 6865, 5695, 6757]`, `[Clips 6865 and 5695]`, `[Clip 6865; Clip 5695, 12:34]`) via `parse_citation_ids`, removes only the invented IDs, and the frontend (`utils/citations.js`) links each ID to `/meeting/<id>` and each timestamp to `/meeting/<id>?t=<seconds>`.
  Anti-hallucination guards (added 2026-06-11):
  - Synthesis runs at `temperature=0.1` with explicit `max_tokens` (the OpenAI default of 1.0 was a major hallucination source)
  - Cosine-distance gate (`RAG_MAX_DISTANCE`, default **0.55** since 2026-09-12; was 0.75) — far-away nearest-neighbor chunks never reach the LLM. Re-calibrated read-only on the prod 512-dim sqlite-vec store with 20 real telemetry questions (top-15 distances 0.18–0.52), 5 borderline (0.32–0.52) and 10 nonsense questions (0.51–0.84): at 0.75 seven of ten nonsense queries still fed chunks to the LLM; 0.55 keeps every real question's top-10. The distribution table lives in the constant's comment.
  - Final context capped at `top_k` (was: unbounded, up to ~180 chunks)
  - Zero surviving chunks → canned no-coverage answer WITHOUT calling the LLM (an empty-context call invites answering from parametric memory)
  - The original question is always embedded alongside the rewrites (lossy rewrites can't sink retrieval)
  - `verify_citations()` strips `[Clip N]` citations whose clip was never retrieved, marks each source `cited: true|false`, and orders cited sources first (frontend collapses the uncited remainder)
  - Multi-turn `/api/chat` condenses follow-ups ("what about the vote?") into standalone questions via gpt-4o-mini before retrieval
- **`rag/server.py`** - FastAPI with `POST /api/ask` and `GET /api/health` endpoints. Singleton OpenAI client, input validation (empty/length), error handling. `/api/health` reports `status` + `jurisdiction` + the running git `sha` (resolved once at startup — proves what deploy-code.sh landed). Also mounts the MCP server (see below) at `/api/mcp` and `/mcp`, threading the FastMCP session manager into the app's lifespan. `POST /admin/reload` clears BOTH `rag.server` and `rag.mcp_server` per-process caches — collection, clip metadata, **and the computed `/api/facets` result** (`_facets_cache`, an in-process cache since facets only change on a reindex but every SPA cold-mount hits it) — the MCP module keeps its own `_collection`/`_clip_metadata`, so forgetting it serves a stale index until restart. `GET /admin/analytics` (same token guard as `/admin/reload` — `RELOAD_TOKEN` + `X-Reload-Token`, origin-only, 404 when unset) reads the telemetry SQLite sink and returns top queries / empty-result rate / volume by transport (http vs mcp) + endpoint / p50-p95 latency / rate-limited count. Forwarded-IP headers (`CF-Connecting-IP`/XFF) are only trusted when the socket peer is loopback/private (our own proxy) — otherwise they're client-spoofable and would bypass rate limits.
- **`rag/telemetry.py`** - Per-query structured logging + a SQLite sink. Each event is logged as a `rag.query {...}` JSON line (journalctl-grep-able) **and** appended to `${LFUCG_OUTPUT_DIR}/telemetry.db` (`rag_events` table, WAL, thread-safe under one lock — callers span the FastAPI threadpool + the MCP asyncio worker threads). The sink is best-effort: any DB failure is logged and swallowed so telemetry can never break a request. `log_query_event(..., model=…)` records the synthesis `model_used` (wired through from `ask()`/`chat()` on the success path). `referer` + `origin` request headers are recorded too (columns added 2026-09-12 by an idempotent `ALTER TABLE … ADD COLUMN` migration in `_migrate()`, guarded by `PRAGMA table_info`; `/admin/analytics` reports `top_referers` by scheme://host). Free-text queries capped at 200 chars; IPs hashed with a daily-rotated salt, never stored raw. The synthesis model is env-configurable via `RAG_SYNTHESIS_MODEL` (resolved at call time inside `ask()`/`chat()` so a `.env` value applies; an explicit `--model` still wins). Backs `GET /admin/analytics`.
- **`rag/prompts.py`** - System prompts for LLM synthesis. Instructs `[Clip ID, MM:SS]` citation format, prefers facts and minutes for precise data. Shared `_GROUNDING_RULES` block (both synthesis prompts): refusal template, no cross-meeting fact fusion, no outside knowledge, exact-name-form attribution (a question's "Shayla Sheehan" must not be confirmed when excerpts only say "Sheehan"), false-premise pushback, placeholder-transcript caveat.
- Vector store: ChromaDB (local, persisted to `lfucg_output/chroma_db/`)
- Embedding model: `text-embedding-3-small` (1536 dims)
- Supports metadata filters: meeting_body, date_after, date_before
- Incremental ingestion tracked via `lfucg_output/rag_state.json`

### Server-Side Search (`rag/search.py`, `scripts/build_search_db.py`)

Replaces the old client-side FlexSearch (40 MB chunked JSON downloaded + indexed on every browser cold-mount). The full-text index now lives server-side in a SQLite FTS5 database (`lfucg_output/search.db`, ~300 MB) and is queried by the RAG server on the Lightsail box:

- **`scripts/build_search_db.py`** — destructive rebuilder. One row per clip with title/transcript/agenda/minutes/facts as searchable columns; speakers/body/date as UNINDEXED filter columns. ~30s on the full archive. Auto-rebuilt by `pipeline.generate_search_index()` and at the end of every `--auto`/`--scrape` batch (per-clip skips the FTS rebuild via `build_search_db=False` to avoid 30s × N).
- **`rag/search.py`** — BM25-ranked search + filters + snippets. Title weighted 10×, facts 5×, speakers 3×, agenda 2×, minutes 1.5×, transcript 1×. Snippets HTML-escaped server-side; sentinel marks (`\x01M\x01`) are reinserted as `<mark>` so the frontend can render via `dangerouslySetInnerHTML` without XSS risk. Query shaping (2026-09-12): `query_tokens` lowercases, drops punctuation-only tokens and English stopwords before the implicit-AND bag-of-words query (a question used to require "what"/"did"/"about" to appear in the clip); `looks_like_question` (ends with `?`, opens with who/what/when/…, or >6 tokens) adds an OR-fallback pass — rows carrying ALL content terms rank first (the "phrase bonus"), then BM25-ranked any-term rows fill the remaining slots. Quoted phrases stay exact. Only ONE `snippet()` column is computed now (`-1` = FTS5 picks the best column) since the UI renders a single snippet.
- **`rag/related.py`** — "more like this" using the centroid of the source clip's existing summary embeddings. Reuses ChromaDB; no extra OpenAI calls.
- **Endpoints (rag/server.py)**:
  - `POST /api/search` — `{q, meeting_body?, speaker?, date_after?, date_before?, limit?}` → ranked clips + pre-marked snippets
  - `GET  /api/suggest?q=...` — autocomplete over titles/bodies/topics/speakers
  - `GET  /api/facets` — populates filter dropdowns (bodies, top-30 speakers by count, date_min/max)
  - `GET  /api/related/{clip_id}` — top-5 similar clips with similarity scores

The `search.db` file lives on the Lightsail box's local disk under `lfucg_output/` (since the 2026-06-11 App Runner→Lightsail migration — no longer baked into a Docker image). The builder writes `search.db.tmp` then `os.replace()` (atomic) so the long-lived server keeps serving the old DB on its cached fd until it reopens; the 6h ingest cron refreshes the live API via token-guarded `POST /admin/reload` (falls back to `systemctl restart lfucg-rag`). `tests/test_search.py` and `tests/test_server_search.py` cover the builder + endpoint surfaces.

### MCP Server (`rag/mcp_server.py`)

Native [Model Context Protocol](https://modelcontextprotocol.io) server exposing the meeting archive as five tools so any MCP-aware client (Claude Desktop, Cursor, NotebookLM, custom agents using the MCP SDK) can query it via the standard protocol instead of HTTP/JSON. Stateless streamable-HTTP transport, CORS-open, no auth — same posture as the `/api/*` HTTP surface.

- **Tools** (each wraps an existing `rag.*` helper — no duplicate retrieval/synthesis logic):
  - `ask_meetings(question, meeting_body?, date_after?, date_before?)` → wraps `rag.query.ask`
  - `search_meetings(q, meeting_body?, speaker?, date_after?, date_before?, limit?)` → wraps `rag.search.search`
  - `find_related_clips(clip_id, limit?)` → wraps `rag.related.related`
  - `get_meeting_clip(clip_id)` → reads `clip_metadata` + `summary.txt` from disk + `granicus_clip_url`
  - `list_recent_meetings(limit?, meeting_body?)` → reads `clip_metadata`, sorted by date desc
- **Mount paths**: `/api/mcp` (CloudFront-routable; only `/api/*` is forwarded to the Lightsail origin) AND `/mcp` (direct origin URL). Mirrors the existing `/ask` ↔ `/api/ask` dual-mount pattern. Both routes wrap the same FastMCP instance, so the lifespan-managed session manager singleton serves both.
- **Tool implementations are module-level functions** (`*_impl`) registered into FastMCP via `add_tool` inside `build_mcp_server()`. Lets tests call tools directly via `mcp_module.search_meetings_impl(...)` without driving the HTTP transport.
- **DNS-rebinding protection disabled** (`TransportSecuritySettings(enable_dns_rebinding_protection=False)`). The endpoint is intentionally public; CORS + Cloudflare WAF handle abuse, and the SDK's default Host-header allow-list would otherwise reject CloudFront's forwarded host.
- **Streamable-HTTP inner path** is set to `/` so the FastAPI mount at `/api/mcp` produces clean URLs (default `/mcp` would mount as `/api/mcp/mcp`).
- **Discovery**: advertised at the top of `llms.txt` (dedicated `## MCP server` section + a top-level link line) and `skill.md` (right above the TL;DR table) so MCP-capable agents discover the protocol path before falling back to the HTTP API.
- **Tests**: `tests/test_mcp_server.py` (33 unit tests on the tool impls — validation, filters, error handling, URL decoration, ordering) and `tests/test_server_mcp_mount.py` (1 integration test on the FastAPI mount + JSON-RPC initialize handshake). Single-mount-test limit is intentional: FastMCP's `StreamableHTTPSessionManager.run()` can only be called once per instance and the server is a module-level singleton.

### Frontend (`frontend/`)

React 18 SPA with:
- Vite build system
- React Router for navigation
- Server-backed full-text search via `useServerSearch` (POST /api/search, debounced 250ms, AbortController on cancel). No client-side index — the homepage loads instantly.
- Autocomplete dropdown via `useSuggestions` (GET /api/suggest, 100ms debounce, ↑/↓/Enter/Esc keyboard navigation)
- Speaker filter dropdown sourced from `useFacets` (GET /api/facets, cached at module level — single fetch on mount)
- "Related meetings" section on the detail page via `useRelatedClips` (GET /api/related/{id}) — **lazy** since 2026-09-12: the request fires only when the section scrolls into view (IntersectionObserver, 200px margin) or the reader clicks "Show related meetings", and never for `navigator.webdriver` clients (it was 94% of API traffic, 58% bots)
- RAG answers link every `[Clip N, MM:SS]` citation (`utils/citations.js`); `/meeting/:id?t=<seconds>` starts the embedded video at that time
- Component-based architecture (MeetingList, MeetingDetail, SearchBar, TopicFilter, AskQuestion)
- **MeetingDetail** has tabbed view: Overview (extracted facts), Transcript (timestamped), Agenda, Official Minutes
- **Overview tab** renders structured `extracted_facts.json` directly — votes with pass/fail badges, financial items, agenda items, public comments, appointments, contested items. Timestamps are clickable (jump to video).
- RAG Q&A interface at `/ask` route with filter dropdowns, source cards, and Granicus video timestamp links

### Pre-rendered meeting pages (`prerender.py`, `seo.py`)

CloudFront serves the SPA shell for every unknown path (S3 404 → `/index.html` 200), and until 2026-09-12 that shell carried a hard-coded homepage `<link rel="canonical">`, so all 2,847 `/meeting/<id>` pages told Google they were the homepage (GSC: 3,340 crawled-not-indexed, 708 duplicates). Now:

- `frontend/index.html` has **no canonical / og:url**; each route sets its own client-side (`MeetingList` → `/`, `MeetingDetail`, `StaticPages`).
- `prerender.py` renders one real HTML document per clip — the live SPA shell (`<site_url>/index.html`, so asset hashes match prod; `PRERENDER_TEMPLATE` overrides) with `<title>`, description, canonical, og:/twitter:, `text/markdown` alternate, the same JSON-LD `@graph` as `utils/seo.js`, and a no-JS body in `<div id="root">` (h1, date/body, disclosure, summary, decisions, Granicus + clip.md + transcript links) that `createRoot().render` replaces on mount. Also the flat static routes (`ask`, `chat`, `about`, `corrections`). Incremental via `lfucg_output/prerender_state.json` (fingerprint = template hash + mtimes of metadata/summary/facts); a new bundle changes the template hash and forces a full pass. Runs at the end of `generate_seo_artifacts` (so every batch / `--generate-index`), or `main.py --prerender [--full]`.
- `sync_data_s3.sh` uploads `lfucg_output/prerender/` to the bucket **root** as extension-less keys (`meeting/<id>`, `Content-Type: text/html; charset=utf-8`) and invalidates `/meeting/*` only when something uploaded; `--prerender-only` skips the /data tree. The exact path now resolves to a 200 document instead of the error fallback.
- **Deploy rule:** `deploy-spa.sh` / `deploy.sh` no longer `--delete` old `/assets/` hashes (pre-rendered pages pin the hash live when they were generated) and exclude the page keys from the root `--delete`. `deploy-spa.sh` regenerates the pages over ssh when `PRERENDER_SSH_HOST` is set; otherwise the box's next ingest cron does it and the old pages keep working meanwhile.

### Scheduled sync — cron on the Lightsail box (`deploy/lightsail/`)

Since the 2026-06-11 App Runner→Lightsail migration, scheduled syncing runs as **cron on the co-located Lightsail box**. All jobs write logs to `/var/log/fuzzy-potato/` (ubuntu-owned — writing to `/var/log` directly fails the redirect-open before `flock`, which silently killed the never-run jobs; fixed 2026-08-08), are `flock`-guarded on `/tmp/lfucg-pipeline.lock`, and use a tri-state heartbeat (`flock -E 99`: success → `<job>`, real failure → `<job>-fail`, lock-skip → nothing). Times are ET:
- **`ingest_cron.sh`** — lean incremental ingest every 6h, **every day** (02/08/14/20): sweep orphaned video intermediates → probe → process new clips → RAG ingest → reload API (`POST /admin/reload`, falls back to `systemctl restart lfucg-rag`) → S3 + CloudFront + feeds.
- **`summaries_cron.sh`** — daily 00:00: two-pass v2 summary + facts for clips the lean ingest left as VTT placeholders, then re-ingest + reload.
- **`backfill_weekly.sh`** — Sunday 03:00: archive-wide `--backfill-docs` + `--backfill-tables-of-motions` + `--retry-failed-sweep`, then reload + sync.
- **`backup_indexes.sh`** — Sunday 04:30: tars `vec.db` (via `sqlite3 .backup`) + `search.db` + `rag_state.json` → `s3://lt-backups-861476138515/fuzzy-potato/<slug>/`.
- telemetry prune — Sunday 05:00: `prune_old_events(90)`.

Full runbook + crontab: `deploy/lightsail/SETUP.md` + `deploy/lightsail/crontab.txt`. **Paris (`civicmemory.news`) was decommissioned 2026-08-08** (reversibly — data in `s3://lt-backups-861476138515/fuzzy-potato/paris/decommission-20260808.tar.gz`, box stopped, DNS+CloudFront down); its multi-jurisdiction code support remains in-tree for revival.

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
  prerender_state.json                    # per-clip fingerprints for the pre-rendered HTML pages
  prerender/meeting/{clip_id}             # pre-rendered HTML page (uploaded to the S3 bucket root)
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
table_of_motions.py                       # Table-of-Motions extraction from agenda packets + facts merge
summary_v2.py                             # Two-pass summary generation module

rag/
  __init__.py
  ingest.py                               # Chunking + embedding + ChromaDB storage
  query.py                                # Retrieval + LLM synthesis logic
  server.py                               # FastAPI endpoints (/ask, /chat, /search, /suggest, /facets, /related) + MCP mount
  search.py                               # SQLite FTS5 BM25 search + suggest + facets
  related.py                              # ChromaDB-backed "more like this" lookup
  mcp_server.py                           # FastMCP server: ask_meetings, search_meetings, find_related_clips, get_meeting_clip, list_recent_meetings
  prompts.py                              # System prompts for synthesis

tests/
  conftest.py                             # Shared fixtures: sample data, mocks, temp dirs
  test_ingest.py                          # Tests for chunking, embedding, ChromaDB storage
  test_query.py                           # Tests for retrieval, filtering, synthesis
  test_server.py                          # Tests for /api/ask, /api/chat (TestClient)
  test_server_search.py                   # Tests for /api/search, /api/suggest, /api/facets, /api/related
  test_search.py                          # Tests for SQLite FTS5 builder + ranking + filters + snippets
  test_mcp_server.py                      # Tests for the 5 MCP tool implementations (mocked rag.* helpers)
  test_server_mcp_mount.py                # Integration test for the FastAPI mount + JSON-RPC initialize handshake
  test_integration.py                     # Tests for main.py pipeline hooks
  test_summary_v2.py                      # Tests for two-pass summary extraction + narration
  test_captions.py                        # Tests for VTT parsing, speaker alignment, garbage-filter
  test_table_of_motions.py                # Tests for Table-of-Motions parsing, clip resolution, merge

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

                                          # (lambda/, Dockerfile, entrypoint.sh, ingest_all.sh — App-Runner-era, DELETED 2026-08-08)

deploy/lightsail/                         # Co-located Lightsail deploy (current prod)
  SETUP.md                                # provisioning + migration runbook
  rag.service.template / lfucg-rag.service # systemd unit (uvicorn rag.server:app)
  Caddyfile(.template)                    # TLS reverse proxy, 404s /admin/* from public
  ingest_cron.sh / summaries_cron.sh / backfill_weekly.sh  # cron jobs (replace Lambda)
  crontab.txt(.template)                  # schedule (6h ingest / daily summaries / weekly sweep)
  sync_data_s3.sh / deploy-spa.sh         # S3 sync of per-clip data + SPA
  provision-jurisdiction.sh               # multi-tenant box bootstrap
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
    "summary": "gpt-4o+claude-haiku-4-5",
    "topics": "gpt-4o-mini"
  }
}
```

`transcript_source` is one of:
- `"whisper-1"` — Whisper transcript only, no captions track available
- `"whisper-1+vtt-speakers"` — Whisper transcript + speaker labels folded in from VTT
- `"granicus_vtt"` — Placeholder transcript synthesized from VTT (Whisper hasn't run yet). On a Whisper-required body this comes with `"whisper_pending": true` when the inline Whisper pass failed; `--retranscribe-placeholders` clears it and stamps `retranscribed_at`.

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

When a clip's motions have been replaced by the official Table of Motions (see `table_of_motions.py`), two extra top-level keys are present and `motions_and_votes` is the authoritative set (movers/seconders/tallies as printed, with video timestamps carried over from the displaced Whisper motions where descriptions aligned):

```json
{
  "motions_source": "table_of_motions",
  "table_of_motions_ref": {
    "source_clip_id": 6779,
    "source_page": null,
    "meeting_date": "2026-05-12"
  }
}
```
