# fuzzy-potato Architecture Review — App Runner → Lightsail co-location + multi-tenancy

Captured during the 2026-06-04 migration planning. Scope: opportunities to
land *during* the App Runner → Lightsail co-location migration, plus a
backlog. Multi-tenant items tagged **[MT]**. Advisory; this doc records the
decisions and what was implemented.

## Context / decisions taken

- **Migration:** co-locate the ingest pipeline + the RAG/MCP API on one AWS
  Lightsail box (Ubuntu, 4GB/2vCPU/80GB, ~$20/mo); retire App Runner + ECR;
  lean incremental ingest cron every 6h on weekdays, heavy sweeps weekly.
  Scaffolding: see `SETUP.md` + the scripts/units in this directory.
- **Why it unlocks cleanups:** the baked-5.4GB-Docker-image pattern, and
  `search.db` being shipped to S3, were *only* contortions for App Runner's
  no-persistent-disk constraint. Co-locating lets the pipeline write
  `lfucg_output/` and the server read it from the same local disk.
- **The one new hazard co-location introduces:** a long-lived server holding
  cached file descriptors / a cached Chroma collection while the pipeline
  rewrites those files underneath it. Addressed below (#3).
- **Cost:** retiring App Runner (1vCPU/4GB always-on ≈ **$25–50/mo**) + ECR
  storage of the 5.4GB image (≈ **$1–3/mo**) for one ~$20/mo box nets
  **~$30–55/mo** and removes the slowest, most failure-prone deploy step.

## Status legend
✅ implemented in this migration · 🔜 do during migration (pending) · 📋 backlog (later)

---

## Done during this migration

### ✅ #3 — Fix the destructive-rebuild-under-a-live-reader hazard (the real one)
`scripts/build_search_db.py` did `unlink()` + recreate while `rag/search.py`
caches the sqlite connection for the process lifetime and never reopens it.
Pre-co-location they never collided (builder on the Mac, server on App
Runner). On one shared box the server's cached fd would point at the
unlinked file → stale results, or "database disk image is malformed" on a
mid-write read. Same staleness class for the cached Chroma `_collection`.
- **Done:** builder now writes `search.db.tmp` then `os.replace()` (atomic) —
  a reader on the old fd keeps serving the complete old DB until it reopens.
- **Done:** added token-guarded localhost `POST /admin/reload` to
  `rag/server.py` that drops the cached collection + clip metadata + sqlite
  connection, so the 6h ingest refreshes data **without** a full restart
  (no dropped in-flight `/api/ask`). Cron prefers it, falls back to
  `systemctl restart`. Caddy 404s `/admin/*` from the public origin; no
  `/api/admin` alias, so it's unreachable via CloudFront.

### ✅ #2 — Stop publishing `search.db` (292MB) to S3 (vestigial)
`deploy.sh`'s top-level data sync excluded `chroma_db/*` and `clips/*` but
not `search.db`, so 292MB shipped to `s3://public-meetings/data/search.db`
every deploy. The SPA uses server-side `POST /api/search`; nothing fetches
it. **Done:** added `--exclude "search.db"` to `deploy.sh`, and the new lean
`sync_data_s3.sh` excludes both `search.db` and `chroma_db/`. *(One-time: delete the stale `s3://public-meetings/data/search.db` object.)*

### ✅ #4 — Move archive-wide sweeps off the 6h path; guard idempotency
`--upgrade-summaries` is resumable (cheap once converged) but
`--backfill-docs --regenerate-summary` re-generates summaries (~$0.15/clip)
for every clip it touches — the only unbounded recurring LLM cost.
- **Done:** the 6h `ingest_cron.sh` runs only probe → process-new → RAG-new.
  `backfill_weekly.sh` runs the sweeps weekly, and **omits
  `--regenerate-summary` by default** (opt-in via `REGEN_SUMMARIES=1`, e.g.
  after a prompt/parser change) — it's the non-idempotent flag.

### ✅ #5 — Memory guard rails + the load-bearing [MT] fact
Chroma is **5.1GB on disk** for ~4,700 clips; HNSW is mmapped (not fully
RAM-resident) so one tenant fits a 4GB box, but the pipeline (yt-dlp/ffmpeg/
Whisper/OCR) is bursty and concurrent.
- **Done:** `lfucg-rag.service` sets `MemoryAccounting=yes` + a **soft**
  `MemoryHigh=3G` (deliberately not a hard `MemoryMax`, which could kill the
  API mid-answer); `SETUP.md` provisions 4GB swap.
- **[MT] load-bearing fact:** one tenant ≈ 5GB Chroma ⇒ **N tenants on one
  4GB box is infeasible** (can't hold two indexes resident). This drives the
  tenancy model to one-box-per-jurisdiction (#11).

### ✅ #12 [MT] — Config-per-jurisdiction NOW-slice (the cheap "don't fork" insurance)
The codebase was mostly env-driven for Granicus already, but had concentrated
single-tenant landmines. **Done:**
- New `config.py` + `jurisdictions/lfucg.toml`. `get_config()` resolves a
  `Jurisdiction` from `jurisdictions/<slug>.toml` (slug = `JURISDICTION` env,
  default `lfucg`), order **env var > TOML > built-in LFUCG default**. LFUCG
  values are also the built-in fallback, so behavior is **byte-identical**
  (verified) even with no TOML present.
- Fixed `probe_clips.py` hard-coding `view_id=14` while `main.py`
  parameterized it — a latent bug that would silently mis-probe any
  jurisdiction whose listing view ≠ 14. Now reads host/view/output from config.
- `rag/ingest.py` `COLLECTION_NAME` now from config (was a global constant).
- `main.py` reads `view_id` default, `first_clip_id`, `granicus_host`,
  `LISTING_VIEW_FALLBACKS`, and the body-parser pattern + acronym sets from
  config (was hard-coded `WQFB|CAC|LFUCG|…`).
- `Dockerfile` ships `config.py` + `jurisdictions/`.
- All 375 tests pass.

---

## Bare process vs container (#1, answered)
**Recommendation taken: bare `uvicorn` + systemd, NOT docker-compose.** The
data lives on the host either way; a container would just bind-mount
`lfucg_output/` and re-add the image-build step we're deleting. `uv` + the
3.11 pin is the reproducibility story; systemd gives `Restart=always`,
memory limits, journald, watchdog for free. The pipeline's system deps
(ffmpeg/tesseract/poppler/yt-dlp) are an apt-install line (see `SETUP.md`).
**[MT]:** keep the `Dockerfile` lean (no `COPY lfucg_output/...`) so it's
reusable as an identical pipeline-runtime image across jurisdiction boxes.

## Reliability posture (#6)
The box is the SPOF for `!ask` (paulBot) + the feeds civic-memory widget —
both *enhancement* surfaces, so degradation (not outage) is the bar.
- systemd `Restart=always`; wire the existing `rag_health` lt-ops check to alert.
- **Back up only the irreplaceable layer:** `clips/` (already mirrored to S3)
  + the small `*.json` state files, nightly to a versioned bucket. Do **not**
  back up chroma/search.db — `search.db` rebuilds from `clips/` in ~30s
  (`--build-search-db`); `chroma_db` re-derives via `--rebuild-rag` (re-embeds,
  costs OpenAI $, but deterministic from `clips/`).
- Verify `!ask` + the feeds widget degrade gracefully on 5xx/timeout.
- **Advise against** multi-box HA / managed vector DB / RDS-for-availability
  at this traffic + budget — over-engineering an enhancement surface.

---

## [MT] Tenancy model (#11): one box per jurisdiction — recommended

- **A — one box per jurisdiction** (isolated data/chroma/search.db/API/domain):
  blast radius = one tenant; fits the 5GB-chroma-per-tenant reality; a
  tenant's pipeline can't OOM another's API; dead-simple. Cost N×$20/mo;
  N boxes to patch (mitigated by one provisioning script + identical image).
- **B — one box, many tenants** (namespaced dirs, per-tenant collections,
  tenant param on every endpoint/MCP tool): **infeasible on 4GB** — can't hold
  two 5GB indexes resident; forces a 16GB+ box (erases the cost win); one
  tenant's sweep starves others; shared-disk failure takes down everyone.
- **C — hybrid** (shared provisioning/code, per-tenant data + API instances):
  this is "A with shared provisioning," which is what we want anyway.

**Decision: A, executed as C** — one Lightsail box per jurisdiction, all built
from the same provisioning script + same code, differing only by a
`jurisdictions/<slug>.toml` + DNS. Cost scales honestly with the data
footprint; blast radius is one city; ops is a `for` loop over boxes. Revisit
multi-tenant-per-box only after a smaller embedding footprint (#13).

### Remaining LATER multi-tenant work (not in the NOW-slice)
- Templatize `deploy.sh` per jurisdiction (S3 bucket, CF dist, ECR/App-Runner
  names — currently `public-meetings` / `E8OIXOXDRETLZ`).
- Per-tenant SPA build + domain; the `meetings.lexingtonky.news` CloudFront +
  DNS are out-of-repo config.
- Generalize `seo.py` (`LFUCG_SITE_URL` is already env-driven; `editor@…`
  hard-coded in ~4 places) + the cross-repo MCP discovery URLs. `config.site_url`
  exists for this but `seo.py` isn't rewired yet (kept the NOW-slice tight).
- Per-tenant rate-limit state: `rag/rate_limit` keys by IP only; namespace by
  tenant once multiple run.

---

## 📋 Backlog (later — explicitly NOT during migration)

### #13 [MT] Vector store / embedding footprint
5.1GB chroma is what makes per-box-per-tenant the only viable model. If you
later want multi-tenant-per-box economics: (a) **truncate
`text-embedding-3-small` to 512-dim** — ~3× smaller vectors, modest recall
loss, a re-embed not a re-architecture; *then* (b) evaluate **sqlite-vec**
(collapses the two data planes into one SQLite file, you already run FTS5).
**Avoid pgvector** (would add Postgres for no other reason). **Advise against
swapping vector stores during the migration** — unforced correctness risk on
the one component that's fine. Dimension-truncation first, deliberately.

### #14 `main.py` decomposition (3,135 lines) — highest-leverage split only
Pull out **`granicus.py`** (URLs/scraping/probing — also the exact seam a
non-Granicus portal would reimplement, so it doubles as the [MT] portal
abstraction), **`metadata.py`** (date/body heuristics — the other
jurisdiction-specific seam), and **`cli.py`** (arg-parse + the ~700-line
dispatch). Leave summary/transcription in place. Not a clean-architecture
rewrite — just isolate the two jurisdiction seams. The #12 config extraction
is the cheap down-payment that makes this easier. Test-backed, after the
migration settles.

### #15 Dual data plane — keep it
Per-clip files on S3/CF (browse/detail/`clip.md`/`llms.txt`) + chroma/search.db
behind the API. **The split is justified — don't collapse it.** `clip.md` /
`llms.txt` are AI-agent discovery surfaces that belong static on S3+CF; the
SPA's per-clip JSON fetches CDN-cache beautifully. Only the hygiene leak (#2)
needed fixing. Routing browse data through the API would add load + a SPOF.

### #16 [MT] Cost-attack surface under flat-rate hosting
On a flat-rate box, a scraper hammering `/api/ask` / `/api/chat` is now a
**cost** attack (OpenAI/Anthropic $/call), not just load. Verify the
disk-persisted rate limiter's limits suit an always-on box and that
Cloudflare (if fronting `meetings.`) has a rule. **[MT]:** namespace rate-limit
state by tenant.

---

## The "if you only do four things" (all ✅ done here)
1. **#3** atomic `search.db` + reload hook — the only new *correctness* hazard.
2. **#1** kill the baked image, go bare systemd — the migration's cost/speed payoff.
3. **#12** config NOW-slice — hours of work that prevents a second-county fork.
4. **#4** split the cron — stops re-billing archive-wide regeneration every 6h.

## Out-of-band note
No `apprunner.yaml`/`.json` in the repo — the App Runner service is configured
via console/CLI (consistent with the dead `lambda/` + nonexistent EventBridge
wiring). Post-migration, **delete** the App Runner service (don't just leave it
idle — it bills on provisioned memory). The `lambda/` dir is dead code; retire
it in a cleanup PR.
