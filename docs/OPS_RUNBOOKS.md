# CivicLens Operational Runbooks

| Field | Value |
|-------|-------|
| Version | 1.0 |
| Last updated | 2026-04-10 |
| Owner | Ops / Platform team |
| Audience | On-call engineers paged outside business hours |
| Companion docs | `docs/OPERATOR_GUIDE.md`, `docs/SCALABILITY.md`, `docs/AUDIT_LOGGING.md`, `docs/DATABASE.md` |

This document is the paged-at-2am reference. It assumes you already read
`docs/OPERATOR_GUIDE.md` during onboarding and know where the source
tree lives. Every command, path, and log field below has been grepped
against the current repository. If a command does not match your
environment, stop and re-verify before running anything destructive.

---

## On-call primer

### Severity definitions

| Sev | Definition | Expected response |
|-----|-----------|-------------------|
| P1 | Customer-visible outage, data loss, or security incident. `/api/v1/ask` or `/api/v1/chat` returning 5xx for multiple tenants, or the API process down. | Page primary on-call immediately. Open incident channel within 5 min. Status page update within 15 min. |
| P2 | Severe degradation for one tenant or one subsystem. Ingestion stalled, webhook flood, runaway rate limits for a single tenant, elevated 5xx rate under 10%. | Acknowledge within 15 min. Resolve or mitigate within 1 business hour. |
| P3 | Latent risk, background job backlog, alert noise. Disk at 75%, a single webhook subscription failing, a slow tenant. | File a ticket by next business day. |

### Escalation matrix

| Problem area | First responder | Escalate to | Final owner |
|--------------|-----------------|-------------|-------------|
| API / server / auth | Platform on-call | Backend tech lead | Platform team |
| Ingestion pipeline (`main.py`) | Platform on-call | Data pipeline lead | Platform team |
| LLM providers (OpenAI / Anthropic) | Platform on-call | Backend tech lead + CTO for quota raises | Backend team |
| Tenant billing / Stripe | Platform on-call | Customer Success lead | Finance + CS |
| SSO / SAML | Platform on-call | Security lead | Security team |
| Infra / disk / host | Platform on-call | Infra lead | Infra team |

### Communication templates

**Slack incident channel opening message**

```
INCIDENT [P1|P2]  <short description>
Started: <timestamp UTC>
Impact:  <what customers see>
IC:      @<your handle>
Scribe:  @<handle or "needed">
Current state: investigating
Next update: in 15 min
```

**Status page update text**

```
We are investigating <degraded service | elevated errors> on the
CivicLens API. Q&A and chat endpoints may be <slow | failing | unavailable>
for some customers. We will post another update within 15 minutes.
```

**Customer email (mitigation reached)**

```
Subject: CivicLens service update

We detected <problem> starting at <time UTC> affecting <scope>.
Mitigation is in place as of <time UTC>. Root-cause analysis is in
progress, and we will share a post-incident summary within 5 business
days. If you continue to see the issue, reply to this email or
contact support@civiclens.io.
```

---

## Tooling cheat sheet

Run from the repository root unless stated. `MEETINGS_OUTPUT_DIR` defaults
to `./meetings_output` (set in `.env`).

```bash
# Public (no auth) lightweight health
curl -sS http://localhost:8000/health

# Authenticated health (counts indexed chunks and clips)
curl -sS -H "X-API-Key: $TENANT_API_KEY" http://localhost:8000/api/v1/health

# Admin detailed health (DB, ChromaDB, memory, disk)
curl -sS -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/api/v1/metrics/health

# Admin metrics snapshot (Prometheus text if prometheus_client installed,
# JSON otherwise). For forced JSON use /api/v1/metrics/json.
curl -sS -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/api/v1/metrics
curl -sS -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/api/v1/metrics/json

# Tail structured JSON logs from the API process
# (api/logging_config.py emits JSON per line with request_id + duration_ms)
journalctl -u civiclens-api -f                        # systemd deploy
docker logs -f civiclens                              # docker deploy

# Tenant management CLI
uv run python -m api.tenants list
uv run python -m api.tenants rotate-key --id <tenant-id>
uv run python -m api.tenants update-plan --id <tenant-id> --plan enterprise

# Audit log ad-hoc query (append-only SQLite, schema in api/audit.py)
# Table: audit_log  Columns: timestamp, tenant_id, action, resource_type,
#                             resource_id, details (JSON), ip_address, request_id
sqlite3 meetings_output/audit.db \
  "SELECT timestamp, tenant_id, action, resource_type, resource_id \
   FROM audit_log ORDER BY timestamp_unix DESC LIMIT 50;"

# Pipeline state (line-one smoke check)
cat meetings_output/state.json | python -m json.tool | head -n 20

# Disk usage by subsystem
du -sh meetings_output/* | sort -h

# Manual ingestion kick
uv run python main.py --auto --max 5
uv run python -m api.ingest --new
```

When uncertain which component holds state, fall back to
`docs/OPERATOR_GUIDE.md` section "Where State Lives".

---

## Runbook 1 - High query latency on `/api/v1/ask` and `/api/v1/chat`

### Symptom / alert condition

p95 latency for `POST /api/v1/ask` or `POST /api/v1/chat` exceeds 8
seconds sustained for 5 minutes. Pulled from the `request_latency:POST:/api/v1/ask`
histogram in `api/metrics.py` (class `MetricsCollector`) or the Prometheus
metric `civiclens_http_request_duration_ms{endpoint="/api/v1/ask"}`.

### Impact

Customer-visible. Browser clients and the embeddable widget time out or
spin. No data loss risk, but this burns the SLA and is a P1 if multiple
tenants are affected simultaneously.

### Severity mapping

- Multiple tenants affected, p95 > 15s: **P1**.
- One tenant or p95 in the 8-15s range: **P2**.
- Single hot question or one noisy client: **P3**.

### Quick triage (first 5 minutes)

1. Pull the admin metrics snapshot:
   ```bash
   curl -sS -H "X-API-Key: $ADMIN_API_KEY" \
     http://localhost:8000/api/v1/metrics/json | python -m json.tool | less
   ```
   Look at `histograms.rag_query_latency.p95`,
   `histograms["request_latency:POST:/api/v1/ask"].p95`, and
   `histograms.embedding_latency.p95`.
2. Pull admin health:
   ```bash
   curl -sS -H "X-API-Key: $ADMIN_API_KEY" \
     http://localhost:8000/api/v1/metrics/health
   ```
   Confirm `checks.chromadb.status == "ok"` and
   `checks.system.memory_percent < 90`.
3. Check the OpenAI and Anthropic status pages (status.openai.com,
   status.anthropic.com). A provider incident is the most common cause.

### Diagnosis steps

Use histogram ratios to localise the slowdown. `rag_query_latency`
covers embed + retrieval + synthesis end-to-end (`api/metrics.py`
`MetricsCollector.record_rag_query`). `embedding_latency` covers only
the `text-embedding-3-small` call. The delta between them is ChromaDB
retrieval plus LLM synthesis.

| Observation | Likely cause |
|-------------|--------------|
| `embedding_latency.p95` normal, `rag_query_latency.p95` high | LLM synthesis (OpenAI `gpt-4o` or Claude Sonnet) is slow |
| `embedding_latency.p95` high | OpenAI embeddings endpoint slow |
| Both low, `request_latency` high | Chroma retrieval or clip metadata loading contention |
| `checks.chromadb` reports error or slow | ChromaDB on-disk store bloated or locked |

Tail structured logs for `ask failed` or `chat failed` lines (raised from
`_handle_ask` / `_handle_chat` in `api/server.py` around line 726 and 793).
Each carries a `request_id` and a `duration_ms`.

### Mitigation

The goal is to bleed off pressure immediately while you diagnose the root
cause.

- Switch the default chat provider from OpenAI to Anthropic (or vice
  versa). The `/api/v1/chat` endpoint accepts `model_provider`
  per-request (see `api/server.py::_handle_chat` and `api/query.py::chat`
  at lines 386-397), so push the frontend default via a feature flag
  if the affected provider is down. The flag management route is
  `PUT /api/v1/flags/{flag_name}` from `api/feature_flags_routes.py`:
  ```bash
  curl -sS -H "X-API-Key: $ADMIN_API_KEY" \
    -X PUT http://localhost:8000/api/v1/flags/chat_default_provider \
    -H "Content-Type: application/json" \
    -d '{"enabled": true, "value": "anthropic"}'
  ```
  (Confirm the flag name and payload shape against your deployed
  `api/feature_flags.py` - flag schemas vary between environments.)
- If ChromaDB retrieval is the hot path, temporarily reduce the top-k
  used by `api/query.py` (`_retrieve_and_prepare`) by deploying a patch
  that lowers `TOP_K` from the current default. This is the only
  mitigation that requires a code change.
- Shed non-essential traffic: tighten CORS via `CORS_ORIGINS` or block
  the embeddable widget endpoints (`/api/v1/widget/ask`,
  `/api/v1/widget/chat`) at the reverse proxy so that authenticated API
  clients get priority.

### Fix

- If the provider is the root cause, stay on the secondary and wait for
  provider recovery. Re-enable the primary once the upstream status
  page is green for 30 minutes.
- If ChromaDB is the bottleneck, schedule a compaction window. See
  Runbook 5 for disk pressure follow-up.
- Long-term: pre-warm the embedding client, cache common questions, and
  measure whether the provider rate limit (Runbook 6) is the real
  constraint.

### Post-incident

- File an incident ticket tagged `latency` and `runbook-1`.
- Capture the latency histogram snapshot at peak and at recovery.
- Notify Customer Success if any tenant raised a support ticket during
  the window.
- Update the status page to resolved.

### Prevention

- Alert on `rag_query_latency.p95` crossing 4s for 5 minutes, not 8s,
  so the page fires before customers notice.
- Add a synthetic probe that calls `/api/v1/ask` every minute with a
  known fixture question.
- Track provider error rates on the vendor status pages via an
  external uptime watcher.

---

## Runbook 2 - Ingestion pipeline stalled (clips not processing)

### Symptom / alert condition

`meetings_output/state.json` field `last_processed_clip_id` has not
advanced in 6 hours during scraping hours, or the EventBridge-triggered
lambda (`lambda/sync_meetings.py`) has logged failures for two
consecutive invocations.

### Impact

No new meetings become searchable. Existing data remains available, so
this is not customer-visible until a new meeting is expected. No data
loss risk as long as Granicus keeps the clip available upstream.

### Severity mapping

- Business hours and backlog growing: **P2**.
- Outside business hours, no customer expectation: **P3**.
- Multiple tenants with active schedules all stalled: escalate to **P1**.

### Quick triage (first 5 minutes)

1. Inspect pipeline state:
   ```bash
   python -m json.tool meetings_output/state.json | head -n 30
   ```
   Note `last_processed_clip_id` and the most recent timestamp.
2. Disk free check (pipeline downloads MP3s and writes PDFs locally):
   ```bash
   df -h $(realpath meetings_output)
   du -sh meetings_output/clips
   ```
3. Scheduler job history (if the scheduler module is loaded):
   ```bash
   sqlite3 meetings_output/scheduler.db \
     "SELECT tenant_id, ran_at, status, error FROM job_runs \
      ORDER BY ran_at DESC LIMIT 20;"
   ```
4. If AWS Lambda is triggering ingestion, pull recent CloudWatch logs
   for `sync_meetings` and look for `yt-dlp`, `ffmpeg`, or `whisper`
   stack traces.

### Diagnosis steps

Rank in order of likelihood.

1. **Granicus upstream unreachable.** Run a probe pass:
   ```bash
   uv run python probe_clips.py $(jq '.last_processed_clip_id' \
     meetings_output/state.json) $(($(jq '.last_processed_clip_id' \
     meetings_output/state.json) + 20))
   ```
   If probe discovers zero new clips, Granicus may not have published
   yet. Not an incident.
2. **OpenAI Whisper quota.** The transcription step calls the Whisper
   API. If this is at quota, you will see HTTP 429 lines in the logs
   from the `whisper-1` model path in `main.py`. See Runbook 6.
3. **Disk full.** ffmpeg compression and PDF OCR fall over silently if
   the temp partition is full. Check `/tmp` and `meetings_output/`.
4. **Scraper selector drift.** If Granicus changed HTML, the yt-dlp
   title extractor or the agenda scraper in `main.py` will fail on
   every clip, not just one. Look for repeated parse errors across
   clip IDs.
5. **Lambda timeout.** `lambda/sync_meetings.py` runs with a wall-clock
   limit. A single slow transcription can blow it.

### Mitigation

- Resume the pipeline by hand on the box that has disk headroom:
  ```bash
  uv run python main.py --auto --max 5
  ```
  Watch it process at least one clip end to end before walking away.
- Skip a poison clip and keep moving:
  ```bash
  uv run python main.py <clip_id> --force --no-audio
  ```
  `--no-audio` avoids re-downloading if the issue is storage, and
  `--force` reprocesses even if partial files exist.
- If lambda is failing, fall back to running the scrape on a long-lived
  host until the lambda is fixed.

### Fix

- Disk full: purge old audio, rotate audit log, see Runbook 5.
- Whisper quota: request a quota raise, see Runbook 6.
- Scraper broken: patch the selector in `main.py`, add a regression
  test under `tests/test_integration.py`, and run
  `uv run python main.py <last_broken_clip> --force` to confirm.
- Lambda timeout: raise `lambda/sync_meetings.py` handler timeout in
  Terraform (`infra/`) and redeploy.

### Post-incident

- Record `last_processed_clip_id` before and after to quantify the gap.
- File a ticket tagged `ingestion` with the failure class.
- If customer-facing meetings were missed, notify Customer Success so
  they can get ahead of support tickets.

### Prevention

- Add an alert on `state.json.last_processed_clip_id` freshness (SLO:
  advanced within 6 hours during active scraping windows).
- Run `probe_clips.py` on a nightly cron independently of the pipeline
  so Granicus availability is monitored separately from transcription.
- Ship disk free alerts at 75% so you never discover disk full via the
  pipeline crashing.

---

## Runbook 3 - Rate limit alerts firing for a single tenant

### Symptom / alert condition

A single `tenant_id` shows an unusually high entry in
`per_tenant.queries` / `per_tenant.errors` in the metrics snapshot, or
the metrics `errors.by_type` counter for `http_429` spikes for that
tenant's typical endpoint. The rate limiter itself raises HTTP 429 from
`api/auth.py` around lines 328-335 when the rolling 30-day count
exceeds `tenant.monthly_limit`.

### Impact

Customer-visible for that tenant only. Their API clients, widgets, and
admin UI will see 429 responses with `Retry-After: 3600`. No data loss.

### Severity mapping

- Paid enterprise tenant hitting the limit unexpectedly: **P2**.
- Starter tenant at their plan cap (expected behavior): **P3** - route
  to Customer Success, not the on-call engineer.
- Suspected API key compromise: **P1**, treat as security incident.

### Quick triage (first 5 minutes)

1. Identify the top-query tenants from the in-process metrics first -
   the audit log records actions but not HTTP status, so metrics is the
   faster signal:
   ```bash
   curl -sS -H "X-API-Key: $ADMIN_API_KEY" \
     http://localhost:8000/api/v1/metrics/json | \
     python -c 'import sys,json; d=json.load(sys.stdin); \
       print(sorted(d["per_tenant"]["queries"].items(), key=lambda x: -x[1])[:10])'
   ```
2. Check the plan on the offending tenant:
   ```bash
   uv run python -m api.tenants list | grep -i <tenant-id>
   ```
   Or fetch via admin API:
   ```bash
   curl -sS -H "X-API-Key: $ADMIN_API_KEY" \
     http://localhost:8000/api/v1/admin/tenants | python -m json.tool
   ```
3. Look at the source IPs and action breakdown for the last hour of
   this tenant's requests (audit schema columns per `api/audit.py`):
   ```bash
   sqlite3 meetings_output/audit.db \
     "SELECT ip_address, action, resource_type, COUNT(*) \
      FROM audit_log \
      WHERE tenant_id = '<id>' \
        AND timestamp_unix > strftime('%s','now') - 3600 \
      GROUP BY ip_address, action, resource_type ORDER BY 4 DESC;"
   ```

### Diagnosis steps

- **Legitimate spike.** Traffic from one or two known IPs, matches a
  scheduled report or data pull. Customer should upgrade their plan.
- **Compromised API key.** Traffic from many unknown IPs, unusual
  geos, or hitting seldom-used endpoints. Treat as P1 security.
- **Billing-driven.** Tenant was recently downgraded (check
  `tenants.db` for `plan` changes) and the new quota is too tight.
- **Buggy integration.** One IP in a tight loop. Usually a misconfigured
  cron or a retry storm on the customer side.

### Mitigation

- For a legitimate spike, temporarily bump the plan ceiling:
  ```bash
  uv run python -m api.tenants update-plan --id <tenant-id> --plan enterprise
  ```
  Document the override so billing reconciles later.
- For suspected compromise, rotate immediately and notify the customer
  before the next business day:
  ```bash
  uv run python -m api.tenants rotate-key --id <tenant-id>
  ```
  Follow up with the customer email template in the on-call primer.
- The in-memory rate limiter in `api/auth.py` (`RateLimiter` class)
  resets on process restart. Do not restart the API process as a
  mitigation - that would hand the abuser a fresh quota.

### Fix

- Legitimate: formal plan upgrade via Stripe portal or
  `/api/v1/admin/tenants/{id}/plan` PATCH.
- Compromise: rotated key plus customer notification plus security
  review of audit trail for any data exfil.
- Integration bug: send the customer the 429 headers documentation and
  ask them to back off or implement retry with jitter.

### Post-incident

- File a ticket tagged `rate-limit` with the tenant ID and diagnosis
  class.
- If rotation happened, open a security incident ticket even if no data
  was confirmed lost. Attach the audit log slice.
- Update Customer Success on plan changes so billing knows.

### Prevention

- Alert Customer Success on soft thresholds (75% of plan quota) so
  legitimate growth is caught before the hard ceiling.
- Publish clear 429 documentation with `Retry-After` semantics.
- The current rate limiter is per-process; for multi-instance
  deployments swap in a shared Redis counter as noted in
  `docs/SCALABILITY.md`.

---

## Runbook 4 - Webhook delivery failures accumulating

### Symptom / alert condition

Error rate on the `webhook_deliveries` table (`api/webhooks.py`, schema
at line 106) exceeds 20% over the last hour. Each row captures one
delivery attempt. Exponential backoff is 1s, 2s, 4s for up to
`MAX_RETRIES = 3` attempts (defined at `api/webhooks.py` lines 34-35).

### Impact

Customer integrations stop receiving events. Not customer-visible in
the CivicLens UI, but partner systems fall out of sync. Risk that the
retry storm floods a customer endpoint and damages our sender reputation.

### Severity mapping

- More than three tenants affected, or one high-profile customer: **P2**.
- One webhook for one tenant failing: **P3**.

### Quick triage (first 5 minutes)

1. Identify the hot webhook (schema from `api/webhooks.py`:
   `webhook_deliveries` has `success` as 0/1 and a `status_code` int):
   ```bash
   sqlite3 meetings_output/webhooks.db \
     "SELECT webhook_id, COUNT(*) AS total, \
             SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failures \
      FROM webhook_deliveries \
      WHERE delivered_at > datetime('now','-1 hour') \
      GROUP BY webhook_id \
      ORDER BY failures DESC LIMIT 10;"
   ```
2. Look at the actual HTTP status codes and response bodies:
   ```bash
   sqlite3 meetings_output/webhooks.db \
     "SELECT delivered_at, status_code, success, attempts, \
             substr(response_body, 1, 200) \
      FROM webhook_deliveries WHERE webhook_id = '<id>' \
      ORDER BY delivered_at DESC LIMIT 20;"
   ```
3. Look up the webhook owner and endpoint URL in the same database
   (`webhooks` table; columns: `id`, `tenant_id`, `url`, `events`,
   `active`) so you know who to contact.

### Diagnosis steps

| HTTP status | Likely cause |
|-------------|--------------|
| Connection error / timeout | Customer endpoint down or firewalled |
| 401 / 403 | HMAC signature mismatch after a secret rotation |
| 404 | Customer changed their endpoint path |
| 5xx from their side | Their receiver is broken |
| 429 from their side | They rate limited us - back off harder |

Signature failures are common right after a customer rotates their
webhook secret without updating our subscription. We sign with
HMAC-SHA256 over the payload.

### Mitigation

- Pause the noisy subscription so we stop flooding the customer. The
  current router in `api/webhook_routes.py` exposes POST, GET, DELETE,
  and a `/test` path but does **not** expose a PATCH to toggle
  `active`. Two options:
  1. Delete and recreate later (destructive, tenant loses the id):
     ```bash
     curl -sS -H "X-API-Key: $TENANT_API_KEY" \
       -X DELETE http://localhost:8000/api/v1/webhooks/<webhook_id>
     ```
  2. Flip `active` in place via direct DB write (reversible, preferred
     during an incident):
     ```bash
     sqlite3 meetings_output/webhooks.db \
       "UPDATE webhooks SET active = 0 WHERE id = '<webhook_id>';"
     ```
- If it is an ingress-side problem on our end (the signing secret in
  our DB drifted from what we sent the customer), block new events
  from going out until the secret is fixed.

### Fix

- Contact the customer and confirm endpoint URL plus current signing
  secret.
- Re-enable the webhook once the receiver is verified:
  ```bash
  sqlite3 meetings_output/webhooks.db \
    "UPDATE webhooks SET active = 1 WHERE id = '<webhook_id>';"
  ```
- Run a test delivery (the webhook test path in `api/webhook_routes.py`
  exposes `POST /api/v1/webhooks/<webhook_id>/test`, which internally
  calls the `webhook.test` delivery in `api/webhooks.py` around line
  472):
  ```bash
  curl -sS -H "X-API-Key: $TENANT_API_KEY" \
    -X POST http://localhost:8000/api/v1/webhooks/<webhook_id>/test
  ```

### Post-incident

- File a ticket tagged `webhooks` with the failure mode.
- Email the customer with: what happened, what events were delayed,
  whether any events were permanently dropped (after three retries).
- Suggest they add retry or idempotency on their side.

### Prevention

- Alert Customer Success on per-webhook delivery failure rate, not just
  aggregate.
- Rotate webhook signing secrets on an announced schedule with
  overlapping validity.
- Cap the retry storm: the current implementation retries three times
  per event. Add a circuit breaker that disables a webhook
  automatically after N consecutive failures over M minutes.

---

## Runbook 5 - Database disk filling up

### Symptom / alert condition

Disk usage on the host running the API process exceeds 80%, or any of
the SQLite files under `meetings_output/` grows abnormally fast. The
`/api/v1/metrics/health` detailed check surfaces `disk_percent` via
psutil and already warns above 90% (`api/metrics.py`, `detailed_health`
around line 444).

### Impact

If the disk fills, SQLite writes fail, ChromaDB cannot compact, and the
ingestion pipeline halts. This becomes P1 very quickly if left to run.

### Severity mapping

- Disk above 90%: **P1**, mitigate immediately.
- Disk 80-90%: **P2**, mitigate within business hours.
- Disk 70-80%: **P3**, plan a cleanup.

### Quick triage (first 5 minutes)

1. Identify the top consumers:
   ```bash
   du -sh meetings_output/* | sort -h
   ```
   Usual suspects in rough order:
   - `clips/` (audio MP3s plus extracted PDFs)
   - `chroma_db/` (vector store)
   - `audit.db` (append-only, grows with every request)
   - `exports/` (async export bundles nobody downloaded)
2. Confirm disk free from the API's own view:
   ```bash
   curl -sS -H "X-API-Key: $ADMIN_API_KEY" \
     http://localhost:8000/api/v1/metrics/health | \
     python -m json.tool | grep -i disk
   ```

### Diagnosis steps

| Bloater | Cause |
|---------|-------|
| `clips/*/audio.mp3` | Pipeline was run without `--no-audio`; audio kept forever |
| `chroma_db/` | Re-embedded the corpus without pruning old entries |
| `audit.db` | Never rotated; one row per request, no retention policy |
| `exports/` | Async export bundles not cleaned up after download |
| `analytics.db` | Event volume grew organically |

List the per-module SQLite files in `docs/OPERATOR_GUIDE.md` under
"Where State Lives" - any of them can in theory grow unbounded.

### Mitigation

- Fastest win: drop stale audio. The metadata records which clip kept
  audio; delete the `.mp3` files in `meetings_output/clips/<id>/` for
  clips older than 90 days. Preserve `metadata.json`, transcripts, and
  the extracted facts JSON.
- Switch future ingest to `--no-audio`:
  ```bash
  uv run python main.py --auto --max 5 --no-audio
  ```
- Rotate the audit log. If the module provides no rotation CLI, take an
  offline copy and truncate in place (schema lives in `api/audit.py`,
  table name is `audit_log`, time field is `timestamp_unix`):
  ```bash
  sqlite3 meetings_output/audit.db ".backup meetings_output/audit-$(date +%F).db"
  sqlite3 meetings_output/audit.db \
    "DELETE FROM audit_log \
       WHERE timestamp_unix < strftime('%s','now') - 90*86400; VACUUM;"
  ```
  Rotation must be coordinated with compliance before deletion - the
  audit log is designed for SOC 2 / FISMA retention. See
  `docs/AUDIT_LOGGING.md`.
- Purge downloaded export bundles older than the TTL:
  ```bash
  find meetings_output/exports -type f -mtime +7 -delete
  ```

### Fix

- Move cold clip artifacts off-host to S3 or equivalent object storage.
  `docs/SCALABILITY.md` describes the migration path: PostgreSQL for
  metadata, S3 for binary artifacts.
- For ChromaDB, the long-term answer is a managed vector store, not
  more disk. Short-term, compaction of the on-disk store helps.
- Set retention SLOs on `audit.db`, `analytics.db`, and `exports/`.

### Post-incident

- File a `disk` ticket with the top-consumer table.
- Update the capacity plan with new growth rates.
- If customer exports were deleted as part of mitigation, notify those
  customers and offer re-exports.

### Prevention

- Alert at 75% disk, not 80%.
- Add a nightly cron that rotates the audit log to a cold archive.
- Add a scheduled job that enforces a TTL on `exports/`.
- Track per-database growth rates weekly so a runaway writer shows up
  before it causes an incident.

---

## Runbook 6 - LLM provider rate limit hit

### Symptom / alert condition

OpenAI or Anthropic return HTTP 429 inside `api/query.py` (ask, chat,
and `synthesize_with_anthropic`), `api/ingest.py` (the
`text-embedding-3-small` calls at `_embed_batch` and `_embed_single`),
or `summary_v2.py` (the two-pass summary narration). The metrics
collector `record_error` counter under `errors.by_type` shows a spike
in provider error types, or 5xxs bubble out of the synthesis path.

### Impact

Customer-visible on the query path (ask / chat) as soon as synthesis
fails. Not immediately visible on the ingestion path - pipeline just
falls behind. LLM cost spikes are a common co-symptom.

### Severity mapping

- Query path failing for users: **P1**.
- Ingestion path only, pipeline catching up on backlog: **P2**.
- One-off 429 burst that self-recovers in minutes: **P3**.

### Quick triage (first 5 minutes)

1. Which provider and which operation?
   ```bash
   curl -sS -H "X-API-Key: $ADMIN_API_KEY" \
     http://localhost:8000/api/v1/metrics/json | \
     python -m json.tool | grep -A3 errors
   ```
2. Read the last 200 lines of the API log for `openai` / `anthropic` /
   `RateLimitError` signatures. Structured logs include `request_id`,
   `path`, and `duration_ms` (see `api/logging_config.py`).
3. Pull the provider dashboards: platform.openai.com/account/limits and
   console.anthropic.com usage pages. Confirm the 429s are ours, not a
   provider-wide incident.

### Diagnosis steps

Three distinct operations hit the LLM providers, and they consume
different quotas:

| Path | Provider | Model | Quota pool |
|------|----------|-------|------------|
| `/api/v1/ask`, `/api/v1/chat` synthesis | OpenAI default (`api/query.py` `DEFAULT_MODEL = "gpt-4o"`), Anthropic on demand | gpt-4o or claude-sonnet | chat completions TPM/RPM |
| Embedding inside ingest and retrieval | OpenAI | text-embedding-3-small | embeddings TPM |
| Two-pass summary narration | Anthropic via `summary_v2.py` | claude-sonnet-4-6 | Anthropic messages TPM |
| Audio transcription in `main.py` | OpenAI | whisper-1 | audio minutes/day |

The triage matters: a Whisper quota exhaustion stalls ingestion only
(Runbook 2 symptoms) while an OpenAI chat quota exhaustion kills live
query traffic.

### Mitigation

- **Query path on OpenAI quota:** force the chat endpoint to Anthropic
  by flipping the default `model_provider` via the frontend flag or by
  hard-coding `model_provider="anthropic"` in the affected client. The
  chat endpoint already supports switching per-request (`api/query.py`
  `chat`, lines 359-397).
- **Embedding quota:** pause ingestion. The query path uses embeddings
  too (one call per question inside `_retrieve_and_prepare`), so
  ingestion must stop first to leave headroom for live queries:
  ```bash
  # If running via scheduler (path from api/scheduler_routes.py)
  curl -sS -H "X-API-Key: $ADMIN_API_KEY" \
    -X POST http://localhost:8000/api/v1/admin/schedule/pause-all
  # Or kill any ad-hoc main.py --scrape loop on the host
  ```
- **Anthropic summary quota:** queue the summary upgrade work. The
  `summary_v2.py` pipeline is invoked explicitly via
  `uv run python main.py --upgrade-summaries`, so simply do not run it
  until quota recovers. Live query paths on Anthropic (chat with
  `model_provider=anthropic`) will still work if there is any headroom.
- **Whisper quota:** see Runbook 2; rerun ingestion once quota
  recovers.

### Fix

- Open a quota raise request with the provider. Anthropic and OpenAI
  both honor these within hours for existing customers if you can point
  to a ramp.
- Add a secondary API key with its own org, split production vs
  pipeline traffic across keys, and failover across keys on 429.
- Introduce a small request queue in front of the ingest path so we do
  not burst against embedding TPM. Document in
  `docs/SCALABILITY.md` as a migration step.

### Post-incident

- File a ticket tagged `llm-quota` with the provider, operation, and
  peak RPM.
- Track the cost impact in Stripe usage metering - a quota incident
  often coincides with a billing anomaly.
- If a customer's chat session visibly failed, notify Customer Success.

### Prevention

- Add alerts on provider error rate per operation (synthesis, embed,
  transcribe, narrate) not just global 5xx.
- Track rolling RPM and TPM against the provider ceiling so you get
  paged at 80% of quota, not at 100%.
- Maintain a documented failover matrix: which chat provider backs up
  which, which embedding provider can substitute, and which operations
  are allowed to fail open.

---

## Post-incident review template

Copy this template into the incident ticket at close. Fill every row.
If a field does not apply, mark it N/A explicitly so reviewers know it
was considered.

```
Incident ID:        INC-YYYY-NNNN
Severity:           P1 / P2 / P3
Summary:            <one sentence>
Runbook used:       <number and name, or "new">
Detected at:        <UTC timestamp>   by <monitor | human | customer>
Mitigated at:       <UTC timestamp>
Resolved at:        <UTC timestamp>
Customer impact:    <count of tenants, what they saw>
Data loss:          yes / no - details
SLA burn:           <minutes of availability lost>

Timeline (UTC):
  HH:MM  event
  HH:MM  event
  HH:MM  event

Root cause:
  <what actually broke, in technical terms>

Contributing factors:
  - <monitoring gap, doc gap, process gap>

What went well:
  - <kept for the blameless review>

What could have gone better:
  - <kept for the blameless review>

Action items:
  - [ ] Owner: <name>  Due: <date>  Item: <short description>
  - [ ] Owner: <name>  Due: <date>  Item: <short description>
  - [ ] Owner: <name>  Due: <date>  Item: <short description>

Communication artifacts:
  - Status page URL:
  - Customer email sent at:
  - Slack incident channel:
```

### Blameless postmortem principles

We assume everyone acted with good intent and the information available
at the time. Reviews focus on the system, the signals, and the
decisions - not on the person holding the pager. If a human error shows
up in the timeline, the question is always "why did the system allow
that error to cause an incident", not "who made the mistake". Action
items fix systems. No names in the root cause.

---

## Next runbooks to write

Backlog. Create tickets against the Ops/Platform board. Ordered by the
current team's estimated likelihood of being paged on them.

1. Backup and restore failure - `api/backup.py` exists but the restore
   path is undocumented.
2. SSO / SAML outage - IdP cert rollover, ACS endpoint rejection, JWT
   secret rotation procedure.
3. Webhook signature rotation procedure (related to Runbook 4 but
   deserves its own step-by-step).
4. Stripe webhook replay and billing reconciliation.
5. ChromaDB corruption recovery (rebuild vs restore).
6. Tenant data export under FOIA deadline pressure.
7. Scheduler drift - APScheduler jobs running twice or not at all.
8. Audit log integrity verification (SHA-256 chain break).
9. PostgreSQL migration cutover from SQLite.
10. Frontend CDN cache poisoning.

End of document.
