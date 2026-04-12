# CivicLens Load Test Report (Template)

This document is a **template**. Populate the `TBD` cells after each real
run so the file becomes a historical record of measured performance. Do not
publish fabricated numbers -- buyers will ask for the raw Locust logs.

## 1. Test Environment

| Field | Value |
|-------|-------|
| Date of run | TBD |
| Git commit SHA | TBD |
| Hardware (CPU / RAM / disk) | TBD |
| OS / kernel | TBD |
| Python version | TBD (target: 3.9+) |
| FastAPI / uvicorn version | TBD |
| Uvicorn workers | TBD (default dev = 1) |
| Database backend | TBD (SQLite `tenants.db` or PostgreSQL via `DATABASE_URL`) |
| ChromaDB version | TBD |
| ChromaDB collection size (chunks) | TBD |
| Tenants provisioned | TBD |
| Tenant plan used for load | TBD (recommend: `enterprise`) |
| OpenAI model(s) in path | `text-embedding-3-small`, `gpt-4o`, `gpt-4o-mini` |
| Anthropic model(s) in path | `claude-sonnet` (chat, when `model_provider=anthropic`) |

## 2. Methodology

| Parameter | Value |
|-----------|-------|
| Load generator | Locust (`scripts/load_test.py`) |
| Virtual users | TBD |
| Spawn rate (users/sec) | TBD |
| Run duration | TBD |
| Think time | `between(1, 3)` seconds per user |
| Task mix (weights) | browse=10, read=5, ask=3, chat=2, votes=1, health=1 |
| Auth | `X-API-Key` header (enterprise tenant) |

### Mix rationale

The weights bias the workload toward read traffic (browse + read meeting =
15/22 ≈ 68%) because public-facing civic portals are overwhelmingly read-heavy.
The RAG endpoints (`ask` + `chat` = 5/22 ≈ 23%) are expensive per request (they
hit embeddings + chat completions), so even at this lower weight they dominate
cost. `health` is included to sanity-check the base uvicorn path with no
auth or DB.

## 3. Results

### Per-endpoint (fill in after each run)

| Endpoint | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error % |
|----------|---------:|----:|---------:|---------:|---------:|--------:|
| `GET /health` | TBD | TBD | TBD | TBD | TBD | TBD |
| `GET /api/v1/search` | TBD | TBD | TBD | TBD | TBD | TBD |
| `GET /api/v1/meetings/{id}` | TBD | TBD | TBD | TBD | TBD | TBD |
| `POST /api/v1/ask` | TBD | TBD | TBD | TBD | TBD | TBD |
| `POST /api/v1/chat` | TBD | TBD | TBD | TBD | TBD | TBD |
| `GET /api/v1/votes` | TBD | TBD | TBD | TBD | TBD | TBD |

### Aggregate

| Metric | Value |
|--------|-------|
| Peak sustained concurrent users (no degradation) | TBD |
| First point of degradation (users at which p95 doubled) | TBD |
| First error threshold (users at which error rate > 1%) | TBD |
| Observed bottleneck (CPU / DB / LLM / embedding / network) | TBD |

### Resource utilization during peak

| Resource | Peak | Notes |
|----------|------|-------|
| API process CPU % | TBD | Single uvicorn worker by default |
| API process RSS | TBD | ChromaDB loads lazily on first `ask` / `chat` |
| `tenants.db` / SQLite WAL size | TBD | |
| ChromaDB disk IO | TBD | |
| OpenAI token spend (run total) | TBD | `embedding` + `chat` |
| Anthropic token spend (run total) | TBD | Only when `model_provider=anthropic` |

## 4. Findings

TBD -- fill in after each run. Example observations to watch for:

- Did `p95 /api/v1/ask` track the upstream OpenAI latency, or did it grow
  faster (implying server-side contention)?
- Was `GET /api/v1/search` CPU-bound on the FTS5 index, or IO-bound on SQLite
  reads?
- Did the SQLite audit log become a hotspot (every request writes to
  `audit.db` via `AuditMiddleware`)?
- Did ChromaDB memory grow linearly with concurrency, or plateau?

## 5. Recommendations

TBD -- fill in after each run.

## 6. Reproducibility

### Prerequisites

1. Activate the project venv: `uv sync --extra api --extra dev`
2. Start the server with a seeded enterprise tenant (see
   `scripts/README_LOAD_TESTING.md` for the `tenants create` invocation).
3. Export the tenant's API key:
   ```bash
   export CIVICLENS_API_KEY=mra_xxxxxxxxxxxxxxxxxxxxxxxx
   ```

### Run the test (headless, 2 minute run, 50 users)

```bash
uv run locust \
  -f scripts/load_test.py \
  --host http://localhost:8000 \
  --users 50 --spawn-rate 5 \
  --run-time 2m \
  --headless \
  --csv meetings_output/load_test_$(date +%Y%m%d_%H%M%S)
```

The `--csv` flag writes three files with raw percentile distributions that
you should attach to this report.

### Re-run at higher load

```bash
uv run locust -f scripts/load_test.py --host http://localhost:8000 \
  --users 200 --spawn-rate 10 --run-time 5m --headless
```

### Interactive web UI

```bash
uv run locust -f scripts/load_test.py --host http://localhost:8000
# Then open http://localhost:8089
```
