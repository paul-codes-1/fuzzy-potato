# LFUCG Meeting RAG API — Integration Guide

A FastAPI service for natural-language Q&A over Lexington-Fayette Urban County Government (LFUCG) city council meetings. Answers are synthesized from a ChromaDB vector store of transcripts, minutes, agendas, and extracted facts. Sources include Granicus video deep-links.

## Base URL

- Local dev: `http://localhost:8000`
- Start server: `uv run uvicorn rag.server:app --reload --port 8000`

CORS is open (`*`) for `GET` and `POST`.

## Endpoints

| Method | Path | Alias | Purpose |
|--------|------|-------|---------|
| GET | `/health` | `/api/health` | Liveness + index stats |
| POST | `/ask` | `/api/ask` | Single-turn Q&A |
| POST | `/chat` | `/api/chat` | Multi-turn conversational Q&A |

The `/api/*` aliases exist for CloudFront routing. Pick either — they return identical responses.

---

### GET /health

No body. Returns:

```json
{
  "status": "ok",
  "chunks_indexed": 48213,   // only after ChromaDB has loaded
  "clips_indexed": 612       // only after metadata has loaded
}
```

`chunks_indexed` and `clips_indexed` are omitted until the first `/ask` or `/chat` call triggers lazy loading. A lone `{"status": "ok"}` is normal at startup.

---

### POST /ask

Single-turn Q&A. No history.

**Request**

```json
{
  "question": "What has the city done about short-term rentals?",
  "meeting_body": "Urban County Council",   // optional exact-match filter
  "date_after": "2024-01-01",               // optional, YYYY-MM-DD, inclusive
  "date_before": "2025-12-31"               // optional, YYYY-MM-DD, inclusive
}
```

Validation:
- `question` is required, trimmed, 1–2000 chars.
- Filter fields are optional. Omit or send `null` to skip.

**Response**

```json
{
  "answer": "The Council passed Ordinance 0042-24 on 2024-06-18 [Clip 6721, 23:14] ...",
  "sources": [
    {
      "clip_id": 6721,
      "date": "2024-06-18",
      "title": "June 18 2024 Council meeting",
      "meeting_body": "Urban County Council",
      "excerpt": "The council voted 12-0 to approve ...",
      "timestamp": 1394,                     // seconds, omitted if unknown
      "granicus_url": "https://lfucg.granicus.com/player/clip/6721?view_id=14&entrytime=1394"
    }
  ],
  "filters_applied": {"meeting_body": "Urban County Council"},
  "chunks_retrieved": 11
}
```

The `answer` text uses inline `[Clip ID, MM:SS]` citation format — agents rendering the answer should preserve these markers or resolve them against `sources`.

---

### POST /chat

Multi-turn Q&A with conversation history. The server retrieves fresh context from the meeting archive for each turn based on the latest user message, then synthesizes against the full (trimmed) history.

**Request**

```json
{
  "messages": [
    {"role": "user", "content": "What did council do about STRs?"},
    {"role": "assistant", "content": "Ordinance 0042-24 passed on..."},
    {"role": "user", "content": "Who voted against it?"}
  ],
  "meeting_body": "Urban County Council",   // optional
  "date_after": "2024-01-01",               // optional
  "date_before": "2025-12-31",              // optional
  "model_provider": "openai"                // "openai" (default) or "anthropic"
}
```

Validation:
- `messages` must be non-empty.
- Last message must have `role: "user"`.
- Each message `role` must be `"user"` or `"assistant"`.
- History is trimmed server-side to the last 10 user/assistant pairs.
- `model_provider`: `"openai"` → `gpt-4o`, `"anthropic"` → `claude-sonnet-4-6`.

**Response**

```json
{
  "role": "assistant",
  "content": "Councilmembers Smith and Jones voted nay [Clip 6721, 24:02] ...",
  "sources": [ /* same shape as /ask sources */ ],
  "model_used": "gpt-4o",
  "filters_applied": {"meeting_body": "Urban County Council"},
  "chunks_retrieved": 9
}
```

Append the returned `{role, content}` to your `messages` array for the next turn. Do not forward `sources`, `model_used`, `chunks_retrieved`, or `filters_applied` back — they are not accepted as input.

---

## Error Handling

| Status | Meaning | Body |
|--------|---------|------|
| 422 | Validation failed (empty question, too long, bad role, etc.) | FastAPI default `{"detail": [...]}` |
| 500 | Server error during retrieval or synthesis | `{"detail": "An error occurred processing your question."}` |

A 500 is opaque by design — check server logs for the stack trace. Retries are safe (endpoints are idempotent).

If the collection is empty, the endpoints still return 200 with `"answer"` / `"content"` explaining that the archive has not been indexed yet and `chunks_retrieved: 0`.

---

## Filter Semantics

- `meeting_body` — exact string match against the `meeting_body` metadata field. Common values: `"Urban County Council"`, `"Planning Commission"`, `"WQFB"`, `"Committee of the Whole"`. No fuzzy matching.
- `date_after` / `date_before` — inclusive string comparison on `YYYY-MM-DD`. Applied together with `$and` if both provided.
- Filters only constrain retrieval. They do not affect synthesis prompting.

---

## Source Types

Each retrieved chunk has a `source` field on the backend (not directly exposed in `sources[]`, but the answer is grounded in a mix):

- `summary` — narrative section from v2 summary, usually with `start_time`
- `facts` — structured votes / financials / attendance / comments
- `minutes` — official meeting minutes
- `agenda` — agenda items
- `transcript` — raw Whisper transcript chunks with timestamps

Retrieval caps at 4 chunks per clip and 2 per source-type per clip to keep diversity. Typical `chunks_retrieved` is 8–15.

---

## Minimal Client Examples

**curl**
```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "budget for parks in 2024", "date_after": "2024-01-01"}'
```

**Python**
```python
import httpx

r = httpx.post("http://localhost:8000/api/ask", json={
    "question": "What zoning changes were approved near Tates Creek?",
    "meeting_body": "Planning Commission",
}, timeout=60)
r.raise_for_status()
data = r.json()
print(data["answer"])
for s in data["sources"]:
    print(f"- {s['title']} ({s['date']}) → {s['granicus_url']}")
```

**Chat loop**
```python
messages = []
while True:
    q = input("> ")
    messages.append({"role": "user", "content": q})
    r = httpx.post("http://localhost:8000/api/chat",
                   json={"messages": messages}, timeout=60)
    reply = r.json()
    print(reply["content"])
    messages.append({"role": "assistant", "content": reply["content"]})
```

---

## Operational Notes

- **First request is slow.** ChromaDB and clip metadata load lazily on the first `/ask` or `/chat` call. Warm it with a cheap question at startup if latency matters.
- **Typical latency:** 2–6 s for `/ask` (query rewrite + embed + 1–3 Chroma queries + GPT-4o synthesis). Anthropic path is comparable.
- **Concurrency:** singletons are not locked, but initialization is idempotent. Concurrent first requests may each build a client; harmless but wasteful. Warm before opening traffic.
- **Required env:** `OPENAI_API_KEY`. `ANTHROPIC_API_KEY` only needed if callers request `model_provider: "anthropic"`. `LFUCG_OUTPUT_DIR` defaults to `./lfucg_output`. `GRANICUS_HOST` (default `lfucg.granicus.com`) and `GRANICUS_VIEW_ID` (default `14`) control the host and view in returned `granicus_url` values; both are read at request time.
- **No auth.** The service assumes a trusted network or upstream gateway. Do not expose directly to the public internet without adding authentication.

---

## Static Data Access

There is no dedicated metadata API. The frontend and any other integrator read meeting data as **static files** served from `/data/*`. In production these are S3/CloudFront objects; in development the `frontend/public/data` path (or a symlink to `lfucg_output/`) is served by Vite.

Use these when you need to browse, list, or render clip data without going through `/ask` — they are faster, cacheable, and return full source material the RAG endpoints only excerpt.

### Paths

| Path | Type | Notes |
|------|------|-------|
| `/data/index.json` | JSON | Full clip catalog with previews |
| `/data/search_index_manifest.json` | JSON | Manifest of FlexSearch chunk files |
| `/data/search_index_chunk_{N}.json` | JSON | FlexSearch chunk (lazy-loaded by frontend) |
| `/data/clips/{clip_id}/metadata.json` | JSON | Per-clip metadata + `files` map |
| `/data/clips/{clip_id}/extracted_facts.json` | JSON | Structured votes, financials, attendance, etc. |
| `/data/clips/{clip_id}/summary.txt` | Text | v2 narrative summary, `##` section headers |
| `/data/clips/{clip_id}/{transcript_filename}` | Text | Whisper transcript (filename from `metadata.files.transcript`) |
| `/data/clips/{clip_id}/{transcript_segments_filename}` | JSON | Timestamped segments |
| `/data/clips/{clip_id}/{agenda_txt_filename}` | Text | Extracted agenda text |
| `/data/clips/{clip_id}/{agenda_pdf_filename}` | PDF | Original agenda PDF |
| `/data/clips/{clip_id}/{minutes_txt_filename}` | Text | Extracted minutes text (when available) |
| `/data/clips/{clip_id}/{minutes_pdf_filename}` | PDF | Original minutes PDF (when available) |
| `/data/clips/{clip_id}/{minutes_html_filename}` | HTML | Original minutes HTML (when available) |

Per-clip filenames are **not fixed** — fetch `metadata.json` first and resolve real paths from its `files` map. Missing files (e.g. a clip with no minutes) simply omit that key.

### `index.json` shape

```json
{
  "generated_at": "2026-04-16T20:25:41.201776",
  "total_clips": 2729,
  "clips": [
    {
      "clip_id": 6747,
      "date": "2026-04-16",
      "meeting_body": "Commission",
      "title": "Planning Commission Work Session (1)",
      "transcript_words": 16860,
      "transcript_preview": "Good afternoon. Today is April 16, 2026...",
      "agenda_preview": "AGENDA Planning Commission Work Session April 16, 2026 ...",
      "summary_preview": "",
      "processed_at": "2026-04-16T20:13:13.449657",
      "files": {
        "audio": "Planning_Commission_Work_Session_audio.mp3",
        "transcript": "transcript_Planning_Commission_Work_Session_audio.txt",
        "transcript_segments": "transcript_Planning_Commission_Work_Session_audio_segments.json",
        "agenda_pdf": "agenda_Planning_Commission_Work_Session.pdf",
        "agenda_txt": "agenda_Planning_Commission_Work_Session.txt"
      }
    }
  ]
}
```

Previews are truncated (first ~500 chars). `files` in `index.json` is a *subset* — it is optimized for listing and may omit fields like `summary_txt`, `extracted_facts`, or `minutes_*`. Use `metadata.json` for the complete map.

### `metadata.json` shape

Per-clip metadata. Full schema is defined in the project's `CLAUDE.md` — summary:

```json
{
  "clip_id": 6747,
  "url": "https://...",
  "date": "2026-04-16",
  "meeting_body": "Commission",
  "title": "Planning Commission Work Session",
  "topics": ["Budget", "Grants", "..."],
  "files": {
    "audio": "...", "transcript": "...", "transcript_segments": "...",
    "summary_txt": "summary.txt",
    "extracted_facts": "extracted_facts.json",
    "agenda_pdf": "...", "agenda_txt": "...",
    "minutes_pdf": "...", "minutes_txt": "..."
  },
  "processed_at": "...",
  "processing_time_seconds": 120.5,
  "transcript_words": 6660,
  "models": {"transcribe": "whisper-1", "summary": "gpt-4o+claude-sonnet", "topics": "gpt-4o-mini"}
}
```

### `extracted_facts.json` shape

Structured facts used by the frontend Overview tab. Top-level keys:

- `meeting_info` — `{date, time, body, presiding_officer, location}`
- `attendance` — `{present: [], absent: [], late: []}`
- `motions_and_votes[]` — each with `identifier`, `description`, `motion_by`, `second_by`, `outcome` (`"passed"` / `"failed"` / etc.), `vote_type`, `ayes`, `nays`, `votes_for[]`, `votes_against[]`, `transcript_approx_time` (`"MM:SS"`)
- `financial_items[]` — `description`, `amount`, `type`, `identifier`, `vendor_or_recipient`
- `public_comments[]` — `speaker`, `topic`, `summary`, `transcript_approx_time`
- `agenda_items[]` — `identifier`, `title`, `type`, `summary`, `key_speakers[]`, `outcome`, `transcript_approx_time`
- `appointments[]`
- `contentious_items[]`

Any section may be empty (`[]` or empty object) — do not assume all keys are populated.

### Transcript segments shape

Array of Whisper segments with seconds-precision timestamps:

```json
[
  {"start": 30.0, "end": 48.8, "text": "Good afternoon."},
  {"start": 48.8, "end": 54.08, "text": "Today is April 16, 2026..."}
]
```

Use `start` to build Granicus deep-links: `https://lfucg.granicus.com/player/clip/{clip_id}?view_id=14&entrytime={int(start)}`.

### Listing meetings without fetching everything

```python
import httpx

idx = httpx.get("https://<host>/data/index.json", timeout=30).json()

# Filter client-side
recent_council = [
    c for c in idx["clips"]
    if c["meeting_body"] == "Urban County Council" and c["date"] >= "2025-01-01"
]
recent_council.sort(key=lambda c: c["date"], reverse=True)

for c in recent_council[:10]:
    print(c["clip_id"], c["date"], c["title"])
```

### Fetching full content for a single meeting

```python
clip_id = 6747
meta = httpx.get(f"https://<host>/data/clips/{clip_id}/metadata.json").json()
base = f"https://<host>/data/clips/{clip_id}"

facts_name = meta["files"].get("extracted_facts")
facts = httpx.get(f"{base}/{facts_name}").json() if facts_name else None

transcript_name = meta["files"].get("transcript")
transcript = httpx.get(f"{base}/{transcript_name}").text if transcript_name else None
```

### Caveats

- **Eventual consistency.** `index.json` is regenerated after each pipeline run; a clip may exist at `/data/clips/{id}/` before the catalog lists it. If you need the freshest view, crawl clip directories directly (e.g. using a known `clip_id` range).
- **Filename assumptions are unsafe.** Titles are slugified into filenames; always resolve via `metadata.files.*`.
- **No search endpoint.** Full-text search is a client-side FlexSearch index (`search_index_chunk_*.json`). For natural-language queries use `/ask`; for keyword listing, filter `index.json` client-side.
- **No auth.** Static buckets are public-read. Treat everything here as public data.
