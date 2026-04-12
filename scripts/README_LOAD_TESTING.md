# Load Testing CivicLens

Operator guide for running `scripts/load_test.py` against a local or staging
CivicLens deployment.

## Prerequisites

1. **Install dependencies including dev extras:**
   ```bash
   uv sync --extra api --extra dev
   ```
   This pulls in `locust` (declared under
   `[project.optional-dependencies.dev]` in `pyproject.toml`).

2. **Provision an enterprise tenant.** The `/api/v1/ask` and `/api/v1/chat`
   endpoints enforce a **sliding 30-day rate limit** per tenant:
   - `starter` = 100 queries / month
   - `pro` = 1,000 queries / month
   - `enterprise` = unlimited

   A 2-minute Locust run at 50 users will comfortably issue more than 1,000
   `/ask` + `/chat` calls. **If you point the test at a `starter` or `pro`
   tenant you will blow the monthly quota in seconds** and every subsequent
   request will return `429 Too Many Requests`, corrupting the report.

   Create an enterprise tenant:

   ```bash
   uv run python -m api.tenants create \
       --id loadtest \
       --name "Load Test Tenant" \
       --granicus-host localhost \
       --granicus-view-id 1 \
       --plan enterprise
   ```

   Copy the `api_key` from the output (it starts with `mra_`).

3. **Seed the tenant with meetings / RAG data** (otherwise `/ask` and
   `/chat` will return empty results but still cost you OpenAI dollars):

   ```bash
   uv run python main.py --tenant-id loadtest --scrape --max 20 --rag
   ```

4. **Start the API server.** For realistic numbers, do **not** use
   `--reload`:

   ```bash
   uv run uvicorn api.server:app --host 0.0.0.0 --port 8000 --workers 1
   ```

   For horizontal-scale experiments, increase `--workers` (see
   `docs/SCALABILITY.md`).

## Setting the API key

Export the key you captured above:

```bash
export CIVICLENS_API_KEY=mra_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

If `CIVICLENS_API_KEY` is unset, the load test sends the literal string
`dev`, which only works if the server is running with the dev-tenant fallback
active (no tenants provisioned and `AUTH_REQUIRED` unset). The dev fallback
*is* mapped to an enterprise plan (see `api/auth.py::_build_dev_tenant`), so
this path is safe for load testing, **but** it can't be used if you have
already created real tenants in `tenants.db`.

## Running the test

### Headless (for CI and reproducible reports)

Short smoke run:

```bash
uv run locust \
  -f scripts/load_test.py \
  --host http://localhost:8000 \
  --users 50 --spawn-rate 5 \
  --run-time 2m \
  --headless
```

With CSV artifacts (recommended for `scripts/load_test_report.md`):

```bash
uv run locust \
  -f scripts/load_test.py \
  --host http://localhost:8000 \
  --users 50 --spawn-rate 5 \
  --run-time 2m \
  --headless \
  --csv meetings_output/load_test_run
```

This writes:

- `meetings_output/load_test_run_stats.csv` -- RPS and response time stats
- `meetings_output/load_test_run_stats_history.csv` -- time series
- `meetings_output/load_test_run_failures.csv` -- errors, if any

### Web UI (for exploratory testing)

```bash
uv run locust -f scripts/load_test.py --host http://localhost:8000
```

Then open <http://localhost:8089> and drive users/spawn-rate interactively.

## Interpreting results

Locust's default table shows RPS, p50 median, p95, p99, and failure rate per
endpoint. The load test script also emits a second per-endpoint summary to
stdout on shutdown (captured via a `locust.events.quitting` listener), in
case you want percentiles computed strictly over the timeslice you care
about.

Key things to look at:

1. **`/health` p95** -- this is your floor. If it's not <10 ms on a modern
   laptop, something is wrong at the network or middleware layer (the
   `AuditMiddleware`, `RequestIDMiddleware`, and `SecurityHeadersMiddleware`
   each add a few tenths of a ms).

2. **`GET /api/v1/search` p95** -- backed by SQLite FTS5 via
   `api/search.py`. Expect sub-50 ms at moderate concurrency; watch for
   climbs as SQLite writers (audit, analytics) contend with search readers.

3. **`POST /api/v1/ask` p95** -- dominated by the round trip to OpenAI
   (embedding call + chat completion). Typical range: 2-6 seconds per
   request. The server is mostly idle while waiting for OpenAI, so high
   concurrency is cheap on the CPU side but expensive on the token side.

4. **`POST /api/v1/chat` p95** -- same as `/ask` plus the multi-turn
   context. Slightly slower on average.

5. **Error rate** -- anything non-zero outside of the rate-limit warmup is
   worth investigating. `429` usually means you are on the wrong plan. `500`
   usually means ChromaDB or the upstream LLM misbehaved.

## Warnings

- **The `/api/v1/ask` and `/api/v1/chat` endpoints spend real money** on
  every request (OpenAI embeddings + chat completions, optionally Anthropic
  on `model_provider=anthropic`). A 5-minute 200-user run can easily issue
  ~10,000 LLM calls. Keep an eye on your provider spend dashboard and
  consider setting provider-side monthly caps before scaling the test.

- **The dev SQLite databases are shared between the load test and real
  workflows.** `audit.db` in particular will grow by one row per request.
  Vacuum or rotate it periodically:
  ```bash
  sqlite3 meetings_output/audit.db "VACUUM"
  ```

- **Do not run the load test against a production tenant** without
  coordinating with on-call. It bypasses no middleware, so every request
  consumes the same rate-limit budget as real customer traffic.

- **Rate limiter state is in-memory.** `api/auth.py::RateLimiter` stores
  request timestamps per tenant and resets on server restart. If you
  restart uvicorn between runs, the quota resets too -- useful for
  repeated experiments with non-enterprise tenants, but remember the
  numbers are not directly comparable across restarts.

## Follow-up reading

- `scripts/load_test_report.md` -- template for recording results
- `docs/SCALABILITY.md` -- forward-looking scalability model
