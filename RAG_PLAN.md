# RAG Q&A Implementation Plan for LFUCG Meeting Archive

## Goal

Add a natural-language Q&A feature to the LFUCG Meeting Archive. Users type a question like "What has the city done about short-term rentals?" and get a synthesized answer citing specific meetings with clickable Granicus video timestamps.

---

## Architecture Overview

```
User question
    │
    ▼
FastAPI backend (/api/ask)
    │
    ├── 1. Embed the question (OpenAI text-embedding-3-small)
    ├── 2. Retrieve top-K chunks from ChromaDB (hybrid: vector + metadata filter)
    ├── 3. Send chunks + question to LLM (gpt-4o via OpenAI API)
    └── 4. Return answer with citations [{clip_id, date, title, timestamp, excerpt}]
    │
    ▼
React frontend (new /ask route with chat-style UI)
```

The system reuses the existing OpenAI API key and fits naturally alongside the current pipeline.

---

## Development Methodology: Red/Green TDD

**Every feature in every phase must be built using strict Red/Green/Refactor TDD.** This is not optional — it is the primary development workflow.

### The cycle

1. **RED** — Write a failing test first. The test defines the expected behavior. Run it. Watch it fail. Do not write any implementation code until you have a failing test.
2. **GREEN** — Write the minimum implementation code to make that test pass. Nothing more.
3. **REFACTOR** — Clean up the implementation and the test. Remove duplication, improve naming, extract helpers. All tests must still pass after refactoring.

### Rules

- **No production code without a failing test.** If you catch yourself writing implementation code first, stop, delete it, and write the test.
- **One behavior per test.** Each test should assert one thing. Name it descriptively: `test_chunk_summary_splits_on_h2_headers`, not `test_chunking`.
- **Tests run fast.** Mock external services (OpenAI API, ChromaDB) in unit tests. Use real instances only in integration tests.
- **Test file mirrors source file.** `api/ingest.py` → `tests/test_ingest.py`, `api/query.py` → `tests/test_query.py`, etc.
- **Run tests after every Green and every Refactor step.** Use `uv run pytest tests/ -x` (stop on first failure) during development.

### Test infrastructure setup (do this first, before Phase 1)

```bash
# Create test directory
mkdir -p tests

# Create conftest.py with shared fixtures
# - Sample metadata.json fixture
# - Sample summary.txt fixture (with known ## sections)
# - Sample transcript_segments.json fixture (with known timestamps)
# - Sample agenda/minutes text fixture
# - Temp directory fixture that mirrors lfucg_output/clips/{clip_id}/ structure
# - Mock OpenAI client fixture (for embedding and chat completion calls)
# - Mock/temp ChromaDB collection fixture (use chromadb ephemeral client)
```

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
rag = [
    "chromadb>=0.4.0",
    "fastapi>=0.100.0",
    "uvicorn>=0.23.0",
]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21.0",
    "httpx>=0.24.0",  # for TestClient with FastAPI
]
```

### What to test in each phase

**Phase 1 (Ingestion) — test the chunking and storage logic:**
- Parsing summary.txt into section chunks (correct boundaries, correct metadata tags)
- Grouping transcript segments into ~500-word passages with overlap
- Passage start/end timestamps match first/last segment
- Agenda/minutes splitting by section headers
- Chunks stored in ChromaDB with correct metadata fields
- Incremental ingestion skips already-ingested clips
- `--stats` output reflects actual collection state
- Edge cases: empty summary, missing transcript_segments file, summary with no `##` headers

**Phase 2 (Query) — test retrieval and synthesis:**
- `build_chroma_filter` produces correct where clauses for each filter combo
- Deduplication keeps max 2 chunks per clip, highest-scored first
- Synthesis prompt includes all required fields (title, date, timestamp)
- Response object has correct structure (answer, sources, granicus_url)
- Filters are correctly passed through to ChromaDB query
- Edge case: no results found returns a clear "not enough information" answer
- Mock the OpenAI chat completion call; assert the messages array is well-formed

**Phase 3 (Server) — test the API layer:**
- `POST /api/ask` returns 200 with valid question
- `POST /api/ask` returns 422 with missing question field
- `GET /api/health` returns chunk/clip counts
- Response JSON matches the documented schema
- Use FastAPI `TestClient` (from httpx) — no real server needed

**Phase 4 (Frontend) — test the React component:**
- Component renders input, submit button, filter dropdowns
- Submitting a question shows loading state
- Successful response renders answer markdown and source cards
- Source cards link to correct `/meeting/{clip_id}` route
- Timestamp badges link to correct Granicus URL with `entrytime` param
- Error state renders user-friendly message
- Use React Testing Library + Vitest (already using Vite)

**Phase 5 (Integration) — test pipeline hooks:**
- `process_clip()` calls `ingest_clip()` when rag_enabled=True
- `process_clip()` does NOT call `ingest_clip()` when rag_enabled=False
- `--rebuild-rag` flag triggers full re-ingestion

### Running tests

```bash
# Run all tests (stop on first failure)
uv run pytest tests/ -x

# Run tests for a specific phase
uv run pytest tests/test_ingest.py -x
uv run pytest tests/test_query.py -x
uv run pytest tests/test_server.py -x

# Run with verbose output
uv run pytest tests/ -x -v

# Frontend tests
cd frontend && npm test
```

---

## Phase 1: Ingestion & Embedding Pipeline

### New file: `api/ingest.py`

Reads existing processed clips and creates embeddings stored in a local ChromaDB collection.

### Chunking strategy (three chunk types)

**1. Summary sections** (highest signal, lowest noise)
- Parse each `summary.txt` by its `## Section Header` boundaries
- Each section becomes one chunk (e.g., "Key Decisions & Votes", "Public Comments", "Controversies")
- Tag with: `clip_id`, `date`, `meeting_body`, `section_type`, `source=summary`
- Typical size: 100–500 words per chunk
- Why: These are dense, already-structured, and cover the most common question types

**2. Transcript passages** (highest detail, noisiest)
- Group consecutive `transcript_segments` into ~500-word passages with ~100-word overlap
- Each passage carries `start_timestamp` and `end_timestamp` from its first/last segment
- Tag with: `clip_id`, `date`, `meeting_body`, `start_time`, `end_time`, `source=transcript`
- Why: Captures detail the summaries miss; timestamps enable deep-linking into video

**3. Agenda + minutes text** (official record)
- Split by natural boundaries (page breaks, section headers) into ~500-word chunks
- Tag with: `clip_id`, `date`, `meeting_body`, `source=agenda` or `source=minutes`
- Why: Grounds answers in the formal record; useful for "what was officially adopted" questions

### Embedding model

- `text-embedding-3-small` from OpenAI (1536 dimensions)
- Cost: ~$0.02 per 1M tokens. Full corpus estimate: ~$1–3 total
- Alternative for zero-cost: `all-MiniLM-L6-v2` via sentence-transformers (384 dims, runs locally)
- Start with OpenAI for quality; can swap later if cost matters

### Vector store: ChromaDB

- Embedded/local mode (no server needed), persisted to `lfucg_output/chroma_db/`
- Collection name: `lfucg_meetings`
- Metadata fields stored per chunk: `clip_id`, `date`, `meeting_body`, `source`, `section_type`, `start_time`, `end_time`
- Why Chroma: simplest setup, good metadata filtering, sufficient for 100K chunks

### Incremental updates

- Track which clip_ids have been ingested in `lfucg_output/rag_state.json`
- After processing a new clip via `main.py`, call `ingest.py` to embed just that clip's chunks
- The Lambda sync handler can call this after each new clip

### CLI commands

```bash
# Full ingest (all existing clips)
uv run python -m api.ingest --all

# Ingest specific clip(s)
uv run python -m api.ingest --clip 6696

# Ingest only new (not yet embedded) clips
uv run python -m api.ingest --new

# Show stats
uv run python -m api.ingest --stats
```

---

## Phase 2: Retrieval & Synthesis Backend

### New file: `api/query.py`

Core query logic, usable from CLI or API.

### Retrieval strategy

```python
def ask(question: str, filters: dict = None, top_k: int = 15) -> Answer:
    # 1. Embed the question
    q_embedding = embed(question)

    # 2. Build metadata filter from optional params
    #    e.g., filters={"meeting_body": "Council", "date_after": "2020-01-01"}
    where_clause = build_chroma_filter(filters)

    # 3. Query ChromaDB for top_k most similar chunks
    results = collection.query(
        query_embeddings=[q_embedding],
        n_results=top_k,
        where=where_clause
    )

    # 4. Deduplicate: if multiple chunks from same clip, keep highest-scored
    #    but include up to 2 chunks per clip for context

    # 5. Synthesize answer via LLM
    answer = synthesize(question, results)

    return answer
```

### Synthesis prompt

Send to `gpt-4o` with a system prompt like:

```
You are a research assistant for Lexington-Fayette Urban County Government meetings.
Answer the user's question based ONLY on the provided meeting excerpts.
Cite specific meetings by date and meeting body.
When a video timestamp is available, include it as [MM:SS] after the citation.
If the excerpts don't contain enough information, say so clearly.
Do not make up information not present in the excerpts.
Format your response in markdown.
```

Each chunk in context formatted as:
```
--- Meeting: {title} | {date} | {meeting_body} | Clip {clip_id} ---
[Source: {source}, Timestamp: {start_time}-{end_time}]
{chunk_text}
```

### Response format

```json
{
  "answer": "Markdown-formatted answer text...",
  "sources": [
    {
      "clip_id": 6696,
      "date": "2026-02-19",
      "title": "Planning Commission Work Session",
      "meeting_body": "Commission",
      "timestamp": 2532,
      "excerpt": "Brief relevant quote...",
      "granicus_url": "https://lfucg.granicus.com/player/clip/6696?view_id=14&entrytime=2532"
    }
  ],
  "filters_applied": {"meeting_body": "Commission"},
  "chunks_retrieved": 15
}
```

### CLI interface

```bash
# Simple question
uv run python -m api.query "What has the city done about short-term rentals?"

# With filters
uv run python -m api.query "budget for parks" --body Council --after 2023-01-01

# Use cheaper model
uv run python -m api.query "when was the Town Branch Trail discussed?" --model gpt-4o-mini
```

---

## Phase 3: FastAPI Server

### New file: `api/server.py`

Lightweight API server so the React frontend can call the RAG system.

### Endpoints

```
POST /api/ask
  Body: { "question": "...", "meeting_body": null, "date_after": null, "date_before": null }
  Response: { "answer": "...", "sources": [...] }

GET /api/health
  Response: { "status": "ok", "chunks_indexed": 85432, "clips_indexed": 4670 }
```

### Running

```bash
# Local development
uv run uvicorn api.server:app --reload --port 8000

# Or integrate with existing frontend dev workflow
# Vite proxy config to forward /api/* to FastAPI
```

### Vite proxy addition (frontend/vite.config.js)

```js
server: {
  proxy: {
    '/api': 'http://localhost:8000'
  }
}
```

---

## Phase 4: React Frontend

### New component: `frontend/src/components/AskQuestion.jsx`

Chat-style Q&A interface accessible from a new route `/ask`.

### UI design

- Text input with "Ask a question about Lexington city meetings" placeholder
- Optional filter dropdowns: meeting body, date range
- Submit button
- Loading spinner while waiting for API
- Answer rendered as markdown
- Sources listed below answer, each with:
  - Meeting title + date (links to `/meeting/{clip_id}`)
  - Timestamp badge (links to Granicus video at that second)
  - Brief excerpt from the matched chunk
- "Ask another question" to reset, or keep conversation context

### Changes to existing files

- `App.jsx`: Add `<Route path="/ask" element={<AskQuestion />} />`
- `MeetingList.jsx` or header: Add navigation link "Ask a Question" alongside existing search
- `index.css`: Styles for the Q&A interface (chat bubbles, source cards)

### No changes needed to

- `MeetingDetail.jsx` (already handles timestamp deep-linking)
- `useMeetings.js`, `useSearch.js`, `useFlexSearch.js` (existing search is separate)
- `main.py` pipeline logic (ingestion is a new parallel module)

---

## Phase 5: Integration with Existing Pipeline

### Modifications to `main.py`

Add one call at the end of `process_clip()`:

```python
# After all other processing steps
if rag_enabled:
    from api.ingest import ingest_clip
    ingest_clip(clip_id, self.output_dir)
```

Gated behind an optional flag so the pipeline works without RAG dependencies installed.

### Modifications to `lambda/sync_meetings.py`

After processing new clips and generating the search index, also run incremental RAG ingestion:

```python
# After generate_search_index()
from api.ingest import ingest_new_clips
ingest_new_clips(output_dir)
```

### New CLI entry point in `main.py`

```bash
uv run python main.py --rebuild-rag    # Re-embed all clips
```

---

## New Dependencies

Add to `pyproject.toml` (note: `rag` and `dev` extras are defined in the TDD section above):

```toml
[project.optional-dependencies]
rag = [
    "chromadb>=0.4.0",
    "fastapi>=0.100.0",
    "uvicorn>=0.23.0",
]
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21.0",
    "httpx>=0.24.0",
]
```

Install with: `uv sync --extra api --extra dev`

This keeps RAG optional — the base pipeline still works without it.

---

## File Structure

```
api/
  __init__.py
  ingest.py          # Chunking + embedding + ChromaDB storage
  query.py           # Retrieval + LLM synthesis logic
  server.py          # FastAPI endpoints
  prompts.py         # System prompts for synthesis (easy to iterate on)

tests/
  conftest.py        # Shared fixtures: sample data, mocks, temp dirs
  test_ingest.py     # Tests for chunking, embedding, ChromaDB storage
  test_query.py      # Tests for retrieval, filtering, synthesis
  test_server.py     # Tests for FastAPI endpoints (TestClient)
  test_integration.py # Tests for main.py/Lambda pipeline hooks

lfucg_output/
  chroma_db/         # ChromaDB persistent storage (gitignored)
  rag_state.json     # Tracks which clips have been ingested

frontend/src/
  components/
    AskQuestion.jsx          # New Q&A interface
    __tests__/
      AskQuestion.test.jsx   # Component tests (Vitest + React Testing Library)
```

---

## Implementation Order

**Every step below follows Red/Green TDD. Write the failing test first, then the implementation, then refactor. No exceptions.**

0. **Test infrastructure** — Create `tests/` directory, `conftest.py` with shared fixtures (sample clip data, mock OpenAI client, ephemeral ChromaDB). Add `pytest` to dev dependencies. Verify `uv run pytest` runs and passes (with zero tests). This must be done before anything else.
1. **`api/ingest.py`** — Start with `tests/test_ingest.py`. Write tests for summary chunking first (RED), implement the parser (GREEN), refactor. Then transcript passage grouping. Then agenda/minutes. Then ChromaDB storage. Then incremental ingestion. Run against the existing 337 transcribed clips as a final integration check.
2. **`api/query.py`** — Start with `tests/test_query.py`. Write tests for filter building (RED/GREEN), then retrieval deduplication, then synthesis prompt construction, then the full `ask()` flow with mocked dependencies. Test via CLI after all unit tests pass.
3. **`api/server.py`** — Start with `tests/test_server.py`. Write tests for each endpoint using FastAPI TestClient (RED/GREEN). Thin wrapper — most logic is already tested in query.py.
4. **`AskQuestion.jsx`** — Add Vitest + React Testing Library to frontend. Write component tests (RED/GREEN) for rendering, loading states, response display, source card links. Can be built in parallel with step 3.
5. **Pipeline integration** — Write tests for the `main.py` and Lambda hooks (rag_enabled flag gating, --rebuild-rag CLI arg). Do last since manual `--rebuild-rag` works fine initially.

---

## Cost Estimates

| Item | One-time | Per query |
|------|----------|-----------|
| Embedding full corpus (~100K chunks) | ~$1–3 | — |
| Embedding per new clip (~30 chunks) | ~$0.001 | — |
| GPT-4o synthesis (8K context) | — | ~$0.03 |
| GPT-4o-mini synthesis (cheaper) | — | ~$0.005 |
| ChromaDB storage | Free (local) | Free |

---

## Future Enhancements (not in this plan)

- Conversation memory (multi-turn Q&A with follow-up questions)
- Hybrid BM25 + vector search for better keyword matching
- Streaming responses (SSE from FastAPI, progressive rendering in React)
- Caching frequent queries to reduce LLM calls
- Admin UI to see which chunks were retrieved (debugging/quality)
