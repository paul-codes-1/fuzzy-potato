# County #2 — Prerequisite Engineering Scope

**Status:** scope/estimate · 2026-06-04 · grounded against the live tree at commit `666cebb`.
**Companion:** `MULTI_COUNTY_EXPANSION_SPEC.md` (the why + roadmap), `ARCHITECTURE_REVIEW.md`, `config.py`/`jurisdictions/lfucg.toml` (the NOW-slice already landed).

> **Purpose.** Define the engineering that must ship *before* a second jurisdiction can go live — independent of which county is first. The expansion spec established the headline constraint: **no Bluegrass neighbor runs Granicus**, so county #2 needs (a) the LFUCG identity lifted out of code into config, and (b) a non-Granicus (YouTube) ingest path behind a portal abstraction. This document breaks that into sized, sequenced workstreams with file-level detail, acceptance criteria, and the load-bearing design risks.
>
> **North-star invariant (every workstream):** LFUCG behavior stays **byte-identical** and the existing **375 tests stay green**. The config NOW-slice already proved this pattern (env > TOML > built-in LFUCG default). Every change here extends it; none changes LFUCG output.

---

## Definition of Done (county #2 is "ready to onboard")

1. A fresh `jurisdictions/<slug>.toml` + `JURISDICTION=<slug>` produces pages, sitemaps, llms.txt, MCP server identity, FastAPI title, and S3/CloudFront targets that say **the new jurisdiction's name** — zero "LFUCG"/"Lexington"/`editor@lexingtonky.news` leakage. (WS1)
2. The ingest pipeline runs against a **YouTube channel** end-to-end: enumerate → download audio → transcribe → extract facts → summarize → ingest → search/ask, with citations linking back to the YouTube timestamp. (WS2+WS3)
3. Table-of-Motions still works where a **structured agenda** source exists (CivicPlus/CivicClerk). (WS4 — required only for counties whose value depends on official motions; can lag the first YouTube onboard.)
4. `provision-jurisdiction.sh <slug>` stands up a box hands-off (WS5 — required before N>~5, not before county #2).
5. Full test suite green + new multi-jurisdiction tests added (WS6, woven through).

---

## Workstream summary & sequencing

| WS | Title | Effort | Risk | Gates |
|----|-------|--------|------|-------|
| **WS1** | Config rewire — lift LFUCG identity into config | **M** (~1–1.5 days) | Low | Gates *any* 2nd jurisdiction (even Granicus). Do first. |
| **WS2** | Extract `VideoSource` protocol + `GranicusSource` | **M** (~1.5–2 days) | Medium | Behavior-preserving refactor; gates WS3. |
| **WS3** | `YouTubeSource` adapter | **L** (~2–4 days) | **High** (clip-id identity model) | The actual unlock for ~15 counties. Depends on WS2. |
| **WS4** | Structured-agenda fetcher (CivicPlus + CivicClerk) | **M** (~2 days) | Medium | Restores Table-of-Motions for YouTube counties. Can follow first onboard. |
| **WS5** | `provision-jurisdiction.sh` | **M** (~1.5 days) | Low | Needed before N>~5, not before #2. |
| **WS6** | Multi-jurisdiction test harness | **S–M** (woven in) | Low | Each WS lands with its tests. |

**Critical path to first YouTube onboard:** WS1 → WS2 → WS3 (+ WS6 throughout). ~5–8 focused days. WS4/WS5 parallelizable / fast-follow.

---

## WS0 — `config.py` field additions (prep for WS1; tiny)

Extend `Jurisdiction` + the TOML loader + `_LFUCG_DEFAULTS` with the fields the rewire consumes. All default to today's LFUCG literals so nothing changes until a second TOML exists.

New fields (with LFUCG defaults): `publication_name="LFUCG Meeting Archive"`, `operator_name="Paul Oliva"`, `editor_email="editor@lexingtonky.news"`, `mcp_url` (derive `f"{site_url}/api/mcp"` — no new field needed), plus the infra/integration fields the provisioner reads but the app mostly doesn't: `s3_bucket`, `cloudfront_dist_id`, `origin_hostname`, `feeds_webhook_url`, and a `source` block (`source_type="granicus"` + a free-form `source_opts` dict) for WS2. `name` already exists.

- **Effort:** S (extend the dataclass + `_load_toml` section map + defaults; mirror the existing pattern). **Acceptance:** `get_config()` returns the new fields; `jurisdictions/lfucg.toml` updated to the extended schema from the spec; existing config test still passes and a new assertion covers the new defaults.

---

## WS1 — Config rewire (lift LFUCG identity out of code)

The exact sites (verified at `666cebb`). Each becomes `get_config().<field>` with the LFUCG default preserved.

### WS1-a · `seo.py` (the biggest surface)
`seo.py` is called with an `output_dir` and builds llms.txt, llms-full.txt, per-clip `clip.md`, and the news sitemap. It needs a config handle. Pass `cfg = get_config()` in (or import it at top — it's cached). Sites:
| Line(s) | Literal | → |
|---|---|---|
| 171, 181, 190, 586 | `editor@lexingtonky.news` | `cfg.editor_email` |
| 432 | `<news:name>LFUCG Meeting Archive</news:name>` | `cfg.publication_name` |
| 447, 637 | `# LFUCG Meeting Archive` (llms.txt / clip.md H1) | `cfg.publication_name` |
| 585 | `Operated by Paul Oliva …` | `cfg.operator_name` |
| 313 | `https://meetings.lexingtonky.news/api/mcp` | `f"{cfg.site_url}/api/mcp"` |
| 338 | `site_url … "https://meetings.lexingtonky.news"` default | `cfg.site_url` (keep `LFUCG_SITE_URL` env as an override for back-compat) |
| (prose) | "Lexington-Fayette Urban County Government" blurbs | `cfg.name` |

- **Effort:** M (most of the work is here — ~10 edits + threading `cfg`). **Risk:** Low, but **high-visibility** (these are the public AI-discovery surfaces — a miss ships "LFUCG" to another county). **Acceptance:** snapshot test — generate seo artifacts with `JURISDICTION=lfucg` (must byte-match today) and with a synthetic `JURISDICTION=testcounty` (must contain the test identity, zero "LFUCG"/"lexingtonky").

### WS1-b · `rag/mcp_server.py`
- L48 `SITE_URL = os.environ.get("LFUCG_SITE_URL", …)` → prefer `get_config().site_url`, keep env override.
- L10 docstring + the ~6 "Lexington-Fayette Urban County" prose mentions in tool descriptions/`instructions` → `cfg.name`. **Caveat:** FastMCP builds tool descriptions at import/`build_mcp_server()` time — confirm `get_config()` is resolvable at that point (it is; config has no heavy deps). **Acceptance:** MCP `initialize` `serverInfo.instructions` reflects the configured jurisdiction; the 33 MCP unit tests stay green.

### WS1-c · `rag/prompts.py`
- The "Lexington-Fayette Urban County Government" mentions in the synthesis system prompts → `cfg.name`. **Risk:** Medium-subtle — prompt wording affects answer quality; keep the sentence structure, swap only the proper noun. **Acceptance:** query tests still pass; spot-check an `/api/ask` answer for a synthetic jurisdiction doesn't say "Lexington".

### WS1-d · `rag/server.py`
- L92 `FastAPI(title="LFUCG Meeting RAG API")` → `f"{cfg.publication_name} RAG API"`. Trivial. **Acceptance:** `/openapi.json` title reflects config.

### WS1-e · `deploy.sh`
- L7 `S3_BUCKET="s3://public-meetings"` → `${S3_BUCKET:-s3://public-meetings}` (env-overridable; the provisioner exports it). L115 help text stays illustrative. `ingest_cron.sh`/`sync_data_s3.sh` already env-default the bucket; **drop the `E8OIXOXDRETLZ` default in `ingest_cron.sh:30`** so a misconfigured box can't invalidate LFUCG's distribution. **Acceptance:** `S3_BUCKET=… CLOUDFRONT_DISTRIBUTION_ID=… ./deploy.sh` targets the right bucket/dist; no LFUCG default reachable.

> **WS1 ships as one PR.** It's the gate for *any* second tenant. After it merges, a Granicus-on-a-different-host county would be pure-config — but since none exist locally, WS1's real job is making WS3's YouTube county not say "LFUCG".

---

## WS2 — Extract `VideoSource` protocol + `GranicusSource` (behavior-preserving)

Introduce `sources/base.py` (the `VideoSource` Protocol + `MeetingRef` dataclass from the spec §2.4) and `sources/granicus.py` (`GranicusSource`) by **moving** these `main.py` methods (verified line numbers) behind the interface:

| `main.py` method | L | Maps to `VideoSource` |
|---|---|---|
| `clip_url` / `agenda_url` / `minutes_url` | 143/147/151 | `canonical_url` + internal URL builders |
| `get_clip_title` | 170 | folded into `get_metadata` |
| `scrape_available_clips` | 204 | `list_meetings` (with `fetch_date_from_listing`) |
| `fetch_date_from_listing` | 871 | `list_meetings` / `get_metadata` (date half) |
| `download_audio` | 241 | `download_audio` |
| `download_agenda` | 684 | `download_agenda` |
| `download_minutes` | 757 | `download_minutes` |
| `fetch_captions` | 1742 | `fetch_captions` (VTT *parsing* stays in `granicus_captions.py`) |
| `scrape_clip_metadata` | 938 | **split**: portal date/source half → source; **body-taxonomy parse stays shared** (already config-driven) |

`LFUCGPipeline.__init__` instantiates `self.source = make_source(self.cfg)` keyed on `cfg.source_type` (default `"granicus"`). Pipeline orchestration (`process_clip`, `auto_process`, `scrape`) calls `self.source.*` instead of the moved methods. **Everything downstream stays put**: `transcribe_audio` (Whisper), `summary_v2`, `table_of_motions`, `rag/*`, `seo.py`.

- **Effort:** M (mechanical move + delegation, but many call sites). **Risk:** **Medium** — lots of internal references; do it test-first and diff a re-process of a known LFUCG clip against its current artifacts. **Acceptance:** (1) `GranicusSource` reprocesses a sample LFUCG clip to byte-identical `metadata.json`/transcript/agenda as before; (2) `test_integration.py` green; (3) `probe_clips.py` (already config-driven) keeps working via `GranicusSource.list_meetings` or stays as-is for Granicus. **Keep this PR strictly behavior-preserving — no YouTube yet.**

---

## WS3 — `YouTubeSource` adapter (the real unlock)

`sources/youtube.py` implementing `VideoSource` against a YouTube channel/playlist via `yt-dlp` (already a dependency). `[source]` TOML: `type="youtube"`, `source.youtube.channel_url=…`, optional playlist filters.

**Method mapping:** `list_meetings` → `yt-dlp --flat-playlist --dump-json <channel>` (enumerate uploads, parse title/date/videoId); `download_audio` → `yt-dlp -x` (already how Granicus audio is pulled — near-identical); `fetch_captions` → `yt-dlp --write-auto-subs --sub-format vtt` (auto-captions; the existing `granicus_captions.py` VTT parser can ingest them, though YouTube auto-caption VTT has no `>> Speaker:` turns, so speaker enrichment degrades to none — acceptable, transcript still flows); `download_agenda`/`download_minutes` → return `{}` (YouTube has no agenda; WS4 supplies it separately); `canonical_url` → `https://youtube.com/watch?v=<id>&t=<sec>` for timestamp-deep-link citations.

### ⚠️ The load-bearing design risk: clip identity
The pipeline assumes a **monotonic integer `clip_id`** in: `state.json.last_processed_clip_id`, `clips/<id>/` dir names, `probe_clips.py` (increments an int range, stops after N 404s), `--auto`/range CLI args, and the `search.db` `clip_id` column. YouTube has **no integer id space to probe** — it's string `videoId`s enumerated from a channel. This is the single hardest part of WS3. Options:

- **(A) Synthetic-id map (recommended).** Assign new YouTube videos a local sequential integer id on first sight; persist a `videoId → clip_id` map in `lfucg_output/source_ids.json`. Keeps the entire integer-id machinery (state, dirs, search.db, --auto) unchanged; only `list_meetings`/`get_metadata`/`canonical_url` know about `videoId`. Discovery model changes from "probe range" to "diff channel listing against the map." **Smallest blast radius.**
- **(B) String ids throughout.** Make `clip_id` a string everywhere. Cleaner conceptually but touches state schema, dir naming, search.db schema, the MCP `get_meeting_clip(clip_id)` tool signature, and every consumer (paulBot, feeds). **Big, breaking, not worth it for #2.**

Go with **(A)**. It localizes YouTube-ness to the adapter and a small id-map, and `--auto` becomes "process any channel videos not yet in the map."

- **Effort:** **L** (the adapter is moderate; the id-model + discovery-loop rework is the cost). **Risk:** **High** — the id model, plus real-world YouTube messiness (members-only/live-still-processing/deleted videos, title formats that don't carry a clean date or body, multi-hour streams). **Acceptance:** cold-start a small real KY county channel (e.g. Paris @CityofParisKY — small archive) end-to-end on a scratch box: `list_meetings` enumerates the channel, N clips process, `/api/search` + `/api/ask` return cited YouTube-timestamp links, no integer-id collisions with a hypothetical Granicus tenant. Unit tests mock `yt-dlp` JSON.

---

## WS4 — Structured-agenda fetcher (restores Table-of-Motions)

YouTube counties have no agenda packets in-band, but the **Table-of-Motions** feature (official motions from the *next* meeting's agenda PDF) is high-value. Add agenda sources decoupled from the video source:

- **CivicPlus/CivicEngage Agenda Center** (Georgetown, Woodford, Clark, Winchester, Frankfort, Boyle, Danville) — scrape the Agenda Center listing → per-meeting agenda PDF; feed the existing `documents.extract_pdf_text` + `table_of_motions` unchanged.
- **CivicClerk** (Paris — `parisky.api.civicclerk.com` exposes a JSON API) — cleanest; fetch agenda/minutes documents via the API.

Model as an optional `AgendaSource` (parallel to `VideoSource`), matched to a meeting by **date + body** (the `table_of_motions.resolve_target_clip` matcher already keys on date+body, so reuse it). Per-jurisdiction config: `[agenda] type="civicplus"|"civicclerk", url=…`.

- **Effort:** M (two fetchers + date/body matching to video clips). **Risk:** Medium (HTML scraping fragility for CivicPlus; CivicClerk API is stable). **Acceptance:** for a county with both YouTube video and a CivicPlus agenda center, a work-session clip gets its official motions backfilled from the next session's agenda, same as LFUCG today. **Can ship after the first YouTube onboard** (county is useful without it; motions are the enrichment).

---

## WS5 — `provision-jurisdiction.sh <slug>` (before N>~5, not before #2)

Parameterize `SETUP.md` into the idempotent script outlined in the spec §2.5 (Lightsail box, static IP, scoped IAM user/policy, S3 bucket, CloudFront dist, Cloudflare DNS public+origin, deploy key, clone, systemd+Caddy, flock cron). For county #2 this can stay **manual** (follow `SETUP.md` by hand, ~the LFUCG runbook minus the seed + plus cold-start). Automate once onboarding cadence justifies it.

- **Effort:** M. **Risk:** Low. **Acceptance:** running it twice is safe (describe-or-create guards); a fresh slug yields a serving box ready for cold-start.

---

## WS6 — Multi-jurisdiction test harness (woven through WS1–WS3)

- A `tests/fixtures/jurisdictions/testcounty.toml` synthetic config + a fixture that sets `JURISDICTION=testcounty` (and clears `get_config`'s `lru_cache`).
- **Identity snapshot tests** (WS1): seo artifacts / MCP instructions / FastAPI title contain `testcounty` identity, zero LFUCG strings.
- **Adapter contract tests** (WS2/WS3): a `FakeSource` exercising the `VideoSource` protocol; `GranicusSource` byte-identical reprocess; `YouTubeSource` with mocked `yt-dlp`.
- **The non-negotiable:** `JURISDICTION=lfucg` (default) output is unchanged and all 375 existing tests stay green. Add `get_config.cache_clear()` to the jurisdiction-switching fixtures (the cache is process-wide).

---

## Risks & calls to make up front

1. **Clip-identity model (WS3)** — decide **(A) synthetic-id map** now; it's the difference between a localized adapter and a breaking schema change that ripples into paulBot + feeds.
2. **`get_config()` resolution timing** — it's import-time-safe (no heavy deps, calls `load_dotenv`), but WS1-b touches FastMCP's import-time description building; verify `JURISDICTION` is in the process env (systemd `EnvironmentFile` injects it before Python starts — good) rather than only in a not-yet-loaded `.env`.
3. **YouTube reliability** — title-date/body parsing is messier than Granicus's structured listings; budget for a per-jurisdiction title-regex in the TOML (`[source.youtube] title_date_pattern=…`) as an escape hatch.
4. **Speaker labels** — Granicus VTT carries `>> Name:` turns; YouTube auto-captions don't. Accept degraded (no per-speaker) transcripts for YouTube counties; the fact-extraction + summary passes don't depend on speaker turns.
5. **Don't gold-plate** — WS4/WS5 are fast-follows, not blockers. The minimum viable county #2 is **WS1 + WS2 + WS3 (option A)** + WS6 tests.

## Recommended PR sequence
1. **PR-1 (WS0+WS1):** config fields + identity rewire + snapshot tests. *Mergeable alone; LFUCG byte-identical.*
2. **PR-2 (WS2):** `VideoSource` + `GranicusSource`, behavior-preserving. *Mergeable alone.*
3. **PR-3 (WS3):** `YouTubeSource` + synthetic-id map + discovery-loop rework. *The unlock.*
4. **PR-4 (WS4):** agenda fetchers. **PR-5 (WS5):** provisioner. *Fast-follows.*

Total critical-path estimate to a first YouTube county: **~1–1.5 engineer-weeks**, dominated by WS3.
