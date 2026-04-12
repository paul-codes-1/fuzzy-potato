# CivicLens Scalability

This document is a forward-looking view of how CivicLens scales from the
single-developer laptop setup all the way to a production, multi-tenant
SaaS deployment. It is grounded in the code as it actually exists today
in this repository (`api/`, `main.py`, `pyproject.toml`) and explicitly
calls out the migration points where a component needs to be swapped for
something different.

**This document describes expected behavior and known architectural
limits. It does not contain measured throughput numbers. Real numbers
belong in `scripts/load_test_report.md` after each Locust run.**

## 1. Architecture at a glance

```
  Client / Widget / SDK
          │
          ▼
   FastAPI (api/server.py)  ── middleware: Audit, RequestID, SecurityHeaders, CORS
          │
          ├── auth         (api/auth.py)     → SQLite tenants.db
          ├── search       (api/search.py)   → SQLite FTS5
          ├── analytics    (api/analytics.py)→ SQLite analytics.db
          ├── audit        (api/audit.py)    → SQLite audit.db (append-only)
          ├── ingest       (api/ingest.py)   → ChromaDB (local, on-disk)
          ├── query        (api/query.py)    → ChromaDB + OpenAI + Anthropic
          └── scheduler    (api/scheduler.py)→ APScheduler (per-tenant cron)

  Pipeline (main.py) -- sync, CLI-invoked
          │
          ├── yt-dlp (audio download)
          ├── ffmpeg (compression)
          ├── Whisper API (transcription)
          ├── OCR / pdfplumber
          ├── GPT-4o / GPT-4o-mini (summary v1 + extraction)
          └── Claude Sonnet (summary v2 narrative)
```

Everything but the LLM APIs runs in a single Python process by default.
That is the starting assumption for the limits below.

## 2. Single-tenant scale (dev defaults: SQLite + local ChromaDB)

This is what you get with `uv run uvicorn api.server:app --port 8000`
and no `DATABASE_URL` set.

### Expected working range

| Dimension | Comfortable | Stressed | Hard ceiling |
|-----------|-------------|----------|--------------|
| Concurrent users hitting `/search`, `/meetings/:id` | tens | ~100 | limited by single-process GIL + SQLite writers |
| Concurrent users hitting `/ask`, `/chat` | bounded by OpenAI token rate more than by the server | | |
| Total indexed chunks in ChromaDB | low six figures | mid six figures | ChromaDB single-node recall latency degrades past roughly 1M chunks |
| Audit log growth | 1 row per request | | audit.db is append-only, plan to rotate |
| `tenants.db` rows | thousands | | SQLite itself scales to millions of rows, bottleneck is writer contention not row count |

### Known bottlenecks at this tier

1. **Single uvicorn worker.** The default `uvicorn api.server:app` command
   starts one worker process. One Python process = one GIL. CPU-bound work
   (serialization, hashing in `audit.py`, pydantic validation) serializes.
   Mitigation: run with `--workers N` (see section 4).
2. **SQLite writer contention.** All of `tenants.db`, `analytics.db`,
   `audit.db`, `scheduler.db`, `sso.db` are SQLite files opened with
   `check_same_thread=False` and `PRAGMA journal_mode=WAL`. WAL mode lets
   many readers coexist with one writer, but the audit middleware writes on
   *every* request, so the audit DB becomes the pressure point first.
3. **ChromaDB loads lazily on the first RAG request.** Cold `/ask` can take
   several seconds longer than warm `/ask` because `get_chroma_collection`
   only runs when `_handle_ask` first calls `_get_collection`
   (`api/server.py`). Pre-warm with a health-check request after restart.

### Recommended SLOs (assume enterprise tenant, warm cache)

These are targets, not measured facts. Use them as acceptance criteria for
the numbers you paste into `scripts/load_test_report.md`:

| Endpoint | p50 target | p95 target |
|----------|-----------:|-----------:|
| `GET /health` | < 5 ms | < 25 ms |
| `GET /api/v1/search` | < 50 ms | < 200 ms |
| `POST /api/v1/ask` | dominated by OpenAI (expect 1.5-4 s) | < 8 s |
| `POST /api/v1/chat` | dominated by OpenAI (expect 2-5 s) | < 10 s |
| `GET /api/v1/votes` | < 50 ms | < 200 ms |

## 3. Multi-tenant scale: when to move to PostgreSQL

`api/database.py` is the abstraction layer that lets CivicLens run on
SQLite (dev) or PostgreSQL (production). Switch to PostgreSQL **before**
any of the following become true:

- You are running more than one uvicorn worker (SQLite's WAL mode works
  across threads in one process; across processes it introduces locking
  latency that compounds under write load).
- Audit-log write volume exceeds a few hundred rows per second sustained
  (roughly what a single modest tenant will produce).
- You need to run multiple API pods/instances behind a load balancer.
  SQLite files are local to one host; PostgreSQL is the moment you go
  horizontal.
- Your `tenants.db`, `analytics.db`, or `audit.db` files exceed a few
  gigabytes and `VACUUM` starts taking long enough to notice.

Migration path:

1. Provision PostgreSQL 14+ with `pgcrypto` available.
2. Run `api/migrations/001_initial.py` and
   `api/migrations/002_digest_subscribers.py` against the PG database.
3. Set `DATABASE_URL=postgresql://user:pass@host:5432/civiclens`.
4. Restart. `api/database.py::init_database` is called from the lifespan
   context in `api/server.py` and will pick up the env var.
5. Re-run `scripts/load_test.py` to confirm the audit write path is no
   longer the bottleneck.

## 4. Horizontal scaling: multiple uvicorn workers behind a load balancer

The default dev command uses one worker. For production:

```bash
uv run uvicorn api.server:app --host 0.0.0.0 --port 8000 --workers 4
```

Or behind gunicorn:

```bash
uv run gunicorn api.server:app \
  -k uvicorn.workers.UvicornWorker \
  -w 4 --bind 0.0.0.0:8000
```

**Before you turn up `--workers`, you must address these in-process
singletons:**

1. **Rate limiter** (`api/auth.py::RateLimiter`) is an in-memory dict.
   With N workers you get N independent rate limit buckets, so a tenant
   on the `pro` plan (1000 queries/month) could effectively get N*1000.
   Fix: back it with Redis, or move the counter into
   `tenants.db` / PostgreSQL.
2. **ChromaDB client** is loaded lazily per-process. N workers = N
   copies of the vector index in RAM. For small collections that's
   fine. For larger collections (see section 5) you want a remote
   vector store anyway.
3. **APScheduler** (`api/scheduler.py`) will try to start in every
   worker, which means each worker competes to run the same cron job.
   Fix: only enable the scheduler in a single "worker 0" process,
   or run scheduled ingestion as a separate service entirely (see
   section 7).
4. **Per-process metrics** (`api/metrics.py`) aggregate locally. Either
   expose `/metrics` per worker and have Prometheus scrape each, or
   use a shared backing store.

Rule of thumb: scale workers up to roughly `2 * CPU_cores`, measure, then
scale horizontally (more instances) only after the LLM rate limits or
the vector store become the binding constraint -- not the FastAPI process
itself. Most real-world CivicLens load is I/O-bound on OpenAI calls, so
a single modest instance goes a long way.

## 5. Vector store scale: ChromaDB → pgvector or Pinecone

ChromaDB running in embedded mode (our current setup, in
`meetings_output/chroma_db/`) is optimized for single-node, low-to-mid
six-figure vector counts. Beyond that:

### Symptoms that you've outgrown local ChromaDB

- First-request load time on cold start exceeds 5-10 seconds (the entire
  collection is memory-mapped on open).
- `/ask` p95 grows faster than `/ask` p50 as concurrency increases --
  a sign that the single-process ChromaDB is queuing similarity searches.
- You need the vector store to survive the API process dying (you
  already get this because Chroma is on-disk, but you lose it the
  moment you go multi-host without shared storage).

### Migration targets

1. **pgvector on PostgreSQL.** Lowest operational overhead if you've
   already migrated to PG for relational data. Latency is good up to
   tens of millions of vectors with an `ivfflat` or `hnsw` index.
   Requires a new ingestion path in `api/ingest.py` that writes
   embeddings into a `vector` column instead of `collection.add()`.
2. **Pinecone / Weaviate / Qdrant Cloud.** Managed service,
   near-unlimited scale, costs money per namespace. Requires
   swapping the `chromadb` client in `api/ingest.py` and
   `api/query.py` for the respective SDK. The rest of the RAG
   pipeline (chunking, prompts, synthesis) is unaffected.
3. **Self-hosted Qdrant or Milvus.** Middle ground. You run a
   separate vector service and point `api/query.py` at its REST/gRPC
   endpoint. More knobs, more ops work, no per-vector billing.

Regardless of destination, keep the 5-source-type chunking taxonomy
in `api/ingest.py` (summary, facts, minutes, agenda, transcript)
intact -- the query layer in `api/query.py` relies on it for filtering.

## 6. LLM rate limits and caching

The RAG path talks to OpenAI (embeddings + chat) and optionally
Anthropic (chat). At any meaningful scale the provider's rate limits
become the dominant constraint, not anything in our own code.

### OpenAI tier limits (as of early 2026, subject to change)

- **Embeddings** (`text-embedding-3-small`): millions of tokens per
  minute at high tiers, rarely a problem in practice.
- **`gpt-4o`** for synthesis: high-tier accounts get hundreds of
  thousands of tokens per minute. A single 2-minute, 200-user
  load test can burn through a multi-hundred-dollar budget.
- **Whisper** for ingestion: generous, but serial (one audio file
  at a time per connection).

### Anthropic limits

Claude Sonnet rate limits are per-workspace and per-model. CivicLens
uses Sonnet for the pass-2 narrative summary and optionally for
`/chat` when `model_provider=anthropic`. Keep the summary pipeline
off the same API key as the user-facing chat if you expect to run
bulk ingestion and live queries at the same time.

### Caching strategy

Today CivicLens **does not cache LLM responses**. The lowest-effort,
highest-return additions:

1. **Cache embeddings for user questions.** `/ask` re-embeds the
   incoming question on every call. A simple Redis/LRU keyed on
   the normalized question text would let identical questions
   (which happen more often than you'd think for popular queries
   like "what did council say about the budget?") skip the
   embedding hop entirely.
2. **Cache retrieval results.** Same key, cache the top-k chunks for
   a short TTL (5-15 minutes). Invalidate on ingest.
3. **Cache full answers for popular questions** with an even shorter
   TTL, with a flag to bypass on demand. Attach a "cached"
   indicator to the response so the UI can tell users.
4. **Use OpenAI's prompt caching** (where available) for the stable
   portion of the synthesis prompt (`api/prompts.py`).

Each layer roughly halves the average request cost of `/ask` once
traffic patterns stabilize.

## 7. Ingestion pipeline: when sync `main.py` becomes the bottleneck

The current `LFUCGPipeline` in `main.py` processes clips **serially** in
whichever process you launch it in. For one tenant with a handful of new
clips a day, that's fine. It stops being fine when:

- You have dozens of tenants, each with its own `scheduler.py`-registered
  cron that can fire at the same time.
- A single clip's processing budget (download + Whisper + OCR + v1
  summary + v2 summary) exceeds the cron interval, so jobs queue.
- You want to decouple ingestion failures from API availability
  (today a crashing ingestion job doesn't affect the API process, but
  a long-running ingestion inside the API process *would*).

### Migration path

1. **Short term: keep `main.py` as the worker, but run it out-of-process.**
   `api/scheduler.py` already invokes the pipeline via
   `LFUCGPipeline`; move that invocation into a subprocess so crashes
   don't take down the scheduler.
2. **Medium term: job queue (Celery / RQ / Dramatiq).** Add a broker
   (Redis is easiest), have the scheduler enqueue jobs instead of
   running them directly, and run N worker processes that pull from
   the queue. This lets you horizontally scale ingestion independently
   from the API and gives you retries + visibility for free.
3. **Long term: event-driven.** Trigger ingestion on a Granicus RSS
   poll hit, not on a cron. Poll Granicus from a lightweight watcher
   (this is essentially what `lambda/sync_meetings.py` does in the
   AWS deployment) and enqueue one job per newly discovered clip.

The frontend never has to know any of this happened, because the
`metadata.json` / `extracted_facts.json` / `summary.txt` output
contract doesn't change.

## 8. Cost model at scale (rough, per query)

**These are estimates based on public provider pricing and typical
CivicLens prompt/response sizes. They are not measured, and they
will drift with provider price changes. Validate against your actual
billing dashboard before quoting any number externally.**

Assumptions:

- Embedding: `text-embedding-3-small`, ~$0.00002 per 1K tokens.
- Synthesis: `gpt-4o`, ~$0.005 per 1K input tokens and ~$0.015 per 1K
  output tokens.
- A typical `/ask` retrieves ~5 chunks of ~300 tokens each = ~1,500
  input tokens, produces ~400 output tokens.
- A typical `/chat` is roughly 2× a single `/ask` because of the
  conversation history.

Per-`/ask` marginal cost (LLM only): roughly $0.015.
Per-`/chat` marginal cost (LLM only): roughly $0.030.

| Queries / day | Monthly LLM cost | Monthly infra cost (1 small VM + SQLite) | Total (rough) |
|--------------:|-----------------:|------------------------------------------:|--------------:|
| 10 | ~$5 | ~$20 | ~$25 |
| 100 | ~$45 | ~$20 | ~$65 |
| 1,000 | ~$450 | ~$50 (bigger VM, managed PG) | ~$500 |
| 10,000 | ~$4,500 | ~$250 (HA PG, Redis, managed vector store, multiple app instances) | ~$4,750 |

**Sanity check against the plan pricing** (`starter=$299`,
`pro=$799`, `enterprise=$2,499`):

- A `starter` customer capped at 100 queries/month pays $299 to cost us
  roughly $1.50 in LLM fees plus infra overhead -- gross margin
  dominated by fixed infra.
- A `pro` customer at their cap of 1,000 queries/month pays $799 and
  costs us roughly $15 in LLM fees; still very profitable.
- An `enterprise` customer with no cap is where margin gets
  interesting. A typical enterprise tenant running 10,000 queries/month
  costs roughly $150 in LLM fees against $2,499 in plan revenue --
  still healthy, but once caching is in place margins improve
  significantly.

## 9. What to measure first

If you have time for exactly one experiment, do this:

1. Seed an enterprise tenant with 20 clips and a realistic ChromaDB
   collection (one `main.py --tenant-id loadtest --scrape --max 20 --rag`).
2. Run `scripts/load_test.py` for 5 minutes at 50 users.
3. Record: `/search` p95, `/ask` p95, error rate, and OpenAI spend.
4. File the result in `scripts/load_test_report.md`.

That single number set will tell you whether your current bottleneck is
in the FastAPI layer, in SQLite, in ChromaDB, or in the LLM provider --
and that determines which section of this document you should act on
first.
