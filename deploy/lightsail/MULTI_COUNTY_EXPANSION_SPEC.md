# Multi-County Expansion Spec — Replicating the Civic-Memory Pipeline Across the Bluegrass

**Status:** draft v1 · 2026-06-04 · authored by an agent team (county-scout / fork-playbook / strategy) coordinated from the LFUCG migration session.
**Companion docs:** `SETUP.md` (the single-box runbook), `ARCHITECTURE_REVIEW.md` (the tenancy decision + multi-tenant analysis this builds on).

> **What this is.** A plan to replicate the `fuzzy-potato` civic-meeting pipeline + RAG/MCP API (now live for Lexington-Fayette on a co-located Lightsail box — see `SETUP.md`) to other Kentucky jurisdictions in the Bluegrass region, **one box per jurisdiction**, onboarded one at a time. It assumes the architecture already chosen in `ARCHITECTURE_REVIEW.md`: **Decision A-executed-as-C** — one Lightsail box per jurisdiction, all built from one provisioning script + one codebase, differentiated only by a `jurisdictions/<slug>.toml` + per-tenant infra. The 5GB-Chroma-per-tenant footprint makes co-tenanting on a 4GB box infeasible, so cost scales linearly per data footprint and blast radius is one city.

---

## 0. Executive Summary — and the finding that changes the plan

**The pivotal finding (from the platform survey, §1): no neighbor jurisdiction runs Granicus.** LFUCG (`lfucg.granicus.com`) is the *only* Granicus instance in the 11-county Bluegrass ring. Of 25 governing bodies surveyed, the split is roughly **~15 on YouTube, ~7 on Facebook Live, 1 on Vimeo, 6 with no video at all** (PDF agendas/minutes only). Structured agenda documents, where they exist, live on **CivicPlus/CivicEngage Agenda Center** or **CivicClerk** — never co-located with the video the way Granicus bundles clip + agenda + captions.

**Why this matters.** The entire current pipeline is built around the Granicus pattern (probe `clip_id`s → `ViewPublisher.php` listings → `player/clip` audio → WebVTT captions → `AgendaViewer.php` packets). That "near-zero-code, just-write-a-TOML" onboard path — the thing that makes LFUCG cheap to run — **transfers to zero neighbors.** Replication is therefore *not* primarily a provisioning exercise; it is gated on **building at least one new ingest adapter.**

**The good news.** The pipeline already uses `yt-dlp`, which pulls YouTube audio + metadata + auto-captions natively. A **YouTube `VideoSource` adapter is the smallest possible delta from the existing code** and unlocks ~15 bodies including the three largest counties in the survey (Madison, Scott, Clark). YouTube auto-captions can even substitute for the Granicus WebVTT speaker track. The playbook (§2) already defines the `VideoSource` abstraction that makes this an *adapter*, not a fork.

**So the real Phase 1 is engineering, not onboarding:**

1. **Config rewire (gating for *any* 2nd jurisdiction, even a hypothetical Granicus one).** Lift the still-hard-coded LFUCG identity out of `seo.py`, `rag/prompts.py`, `rag/mcp_server.py`, `rag/server.py`, and `deploy.sh` into `config.py`/the TOML — otherwise county #2's pages say "LFUCG Meeting Archive" and email `editor@lexingtonky.news`. (See §2.2; these are the "remaining LATER MT items" the architecture review parked.)
2. **Extract `GranicusSource` behind a `VideoSource` protocol** (§2.4) — a near-verbatim lift of today's methods, test-backed. This is the seam.
3. **Build the `YouTubeSource` adapter** — the first non-Granicus source; unlocks the bulk of the region.
4. **Build a structured-agenda fetcher** (CivicPlus Agenda Center + CivicClerk) to preserve the Table-of-Motions feature, which depends on machine-readable agenda packets.
5. *Then* the per-jurisdiction work becomes config + infra + a cold-start backfill, and the strategy section's cost/sequencing framework (§3) applies.

**Recommended first onboards** (once the YouTube adapter exists), by population × archived/structured video × structured-agenda availability:
- **Madison County FC** (92,701 — largest; already keeps a searchable agenda+minutes+video archive),
- **Scott County FC + Georgetown** (paired whole-county onboard; Georgetown adds a CivicPlus agenda center),
- **Boyle County FC + Danville** (cleanest "YouTube + CivicPlus agendas" pairing),
- **Paris** (smallest but fully structured: YouTube + a CivicClerk JSON API — the lowest-friction proof-of-concept).

**Cost shape** (§3): one box ≈ **$24/mo infra + a one-time cold-start LLM bolus** of ~$0.55–0.95/clip (Whisper-dominated; ~$550–950 per 1,000 clips). Most neighbors have far shallower archives than Fayette, so realistic cold starts are **$200–700**, not thousands. A 5-county footprint ≈ **~$165/mo steady-state** + one-time backfills. Branding: a neutral umbrella (`<county>.civicmemory.news`) rather than Lexington-centric subdomains.

The three detailed sections follow, verbatim from the authoring agents; §4 gives the reconciled, sequenced roadmap.

---

# 1. Target Jurisdictions & Video-Platform Survey

**Headline finding — Granicus is a Lexington outlier, not a regional norm.** Of the 25 governing bodies surveyed across the 11-county Bluegrass ring around Fayette, **none run Granicus or Legistar.** LFUCG (`lfucg.granicus.com`) appears to be the only Granicus instance in the region. Neighbors split between **YouTube** (the dominant video host), **Facebook Live**, **Vimeo** (one case), and **no video at all** (PDF agendas/minutes only). Agenda documents, where structured, live on **CivicPlus/CivicEngage Agenda Center** or **CivicClerk** — never co-located with the video the way Granicus co-locates clip + agenda + captions. The practical consequence: the "near-zero-code Granicus onboard" path the pipeline was built around **does not exist for any neighbor jurisdiction**. Replication requires building at least one new ingest adapter (a YouTube adapter is by far the highest-leverage), plus a separate agenda-document adapter for the Table-of-Motions feature.

**Granicus host-test methodology.** Calibrated against the real LFUCG host: valid Granicus instances return HTTP 200 with a `listingTable` of `clip_id` refs on `ViewPublisher.php?view_id=N`; unprovisioned subdomains 302→`/core/error/NotFound.aspx`→404 for every view_id. ~40 plausible KY slugs were probed. **Every KY target returned NotFound.** The only HTTP-200 hits were same-name jurisdictions in other states — flagged below so they aren't mis-mapped.

## Master survey table

| Jurisdiction | Type | Pop. (2020) | Video platform | Agenda platform | Granicus host? | Portal / channel | Cadence |
|---|---|---|---|---|---|---|---|
| **Jessamine County** | Fiscal court | 52,991 | none-found | ecclix subscription portal | No | jessamineky.gov | 1st/3rd/5th Tue 4pm |
| **Nicholasville** | City commission | 31,490 | **Vimeo** (BGADD regional channel) | documents-on-demand.com | No | vimeo.com/bgadd | 2nd & 4th Mon 5pm |
| **Wilmore** | City council | 5,999 | none-found (FB page only) | wilmore.org (PDF) | No | wilmore.org/agendas-and-minutes | 1st & 3rd Mon 6pm |
| **Scott County** | Fiscal court | 57,155 | **YouTube** (live+archive) | scottky.gov (PDF) | No | youtube.com/channel/UCbwvLVLIFKm1jzaOED0vQxA | Work 1st Fri 9am; reg 2nd Fri 9am & 4th Thu 7pm |
| **Georgetown** | City council | 37,086 | **YouTube** | CivicPlus Agenda Center | No | youtube.com/@CityofGeorgetownKY | 2nd & 4th Mon |
| **Woodford County** | Fiscal court | 26,871 | **YouTube + Facebook** | CivicPlus Agenda Center | No | youtube.com/@WoodfordCountyKentucky | Monthly + specials |
| **Versailles** | City council | 10,347 | **Facebook** (since Nov 2019) | KLC/Sophicity site | No | facebook.com/versailleskentucky | 1st & 3rd Tue |
| **Midway** | City council | 1,741 | **Facebook** (embedded) | Sophicity/KLC site | No | facebook.com/midwaygov | ~bi-weekly |
| **Clark County** | Fiscal court | 36,972 | **YouTube** (live+archive) | CivicPlus Agenda Center | No (clark.legistar.com = Clark Co **NV**) | youtube.com/channel/UCSXXInUlwJ70ZcjDb6CisWw | 2nd Wed 8:30am + 4th Thu 5:30pm |
| **Winchester** | Board of Commissioners | 19,134 | **Facebook Live** | CivicPlus Agenda Center | No | facebook.com/WinKyCityHall | 2 regular mtgs/month |
| **Bourbon County** | Fiscal court | 20,252 | **Facebook Live** | Wix site (BGADD-managed) | No | bourbonky.com | 2nd Thu 3pm + 4th Thu 5pm |
| **Paris** | City commission | 10,171 | **YouTube** (@CityofParisKY) | **CivicClerk** (parisky.portal.civicclerk.com) | No | youtube.com/@CityofParisKY | typ. 9am |
| **Harrison County** | Fiscal court | 18,692 | **YouTube** + Facebook | own site (expired cert) | No | youtube.com/channel/UC3dvMQRuRnRr4rD7pLgjA7w | 2nd & 4th Tue 5:30pm |
| **Cynthiana** | Board of Commissioners | 6,333 | **YouTube** | Apptegy site (cynthianaky.com) | No | cynthianaky.com/live-feed | 1st & 3rd Tue 5:30pm |
| **Madison County** | Fiscal court | 92,701 | **YouTube + Facebook Live** | own site, searchable archive table | No | youtube.com/madisoncountyky | 2nd & 4th Tue 9:30am |
| **Richmond** | City commission | 34,585 | **YouTube** (now) + **Vimeo** (legacy) | richmondky.gov | No (richmond.granicus.com = Richmond **CA**) | youtube.com/@CityofRichmondKY | bi-weekly Tue |
| **Berea** | City council | ~14,300 | **YouTube** (streams) | bereaky.gov (WordPress) | No (cityofberea.org = Berea **OH**) | youtube.com/@cityofbereakentucky40403 | 1st & 3rd Tue 6:30pm |
| **Franklin County** | Fiscal court | 51,541 | **Facebook Live + YouTube** | franklincounty.ky.gov (PDF) | No (fcoh.granicus.com = Franklin Co **OH**) | facebook.com/fcfcky | bi-weekly 5pm |
| **Frankfort** (state capital) | City commission | 28,602 | **Facebook Live/Watch** + Plant Board Cable Ch.10 | CivicPlus Agenda Center | No | frankfort.ky.gov/809/Videos-Recordings | ~3rd Mon |
| **Mercer County** | Fiscal court | 23,772 | **Facebook Live** | mercercounty.ky.gov | No (mercercounty.granicus.com = Mercer Co **NJ**) | 2nd & last Tue 10am |
| **Harrodsburg** | City commission | 9,064 | none-found (FB page only) | harrodsburgky.gov (PDF) | No | harrodsburgky.gov/AgendaMinutes | 2nd & 4th Mon noon |
| **Garrard County** | Fiscal court | 16,953 | none-found | garrardcountyky.gov (PDF) | No | garrardcountyky.gov/agenda | 2nd Mon 6pm + last Mon 4pm |
| **Lancaster** | City council | 3,901 | none-found | cityoflancasterky.com (PDF) | No | cityoflancasterky.com/meeting-documents | monthly |
| **Anderson County** | Fiscal court | 23,852 | none-found | andersoncounty.ky.gov (PDF) | No | andersoncounty.ky.gov (Fiscal-Court-Archive) | ~1st Tue 10am |
| **Lawrenceburg** | City council | 11,728 | **YouTube** (city-run, back to 2018) | lawrenceburgky.org | No (excl. Lawrenceburg **IN**) | lawrenceburgky.org → YouTube | 1st & 3rd Mon |
| **Boyle County** | Fiscal court | 30,614 | **YouTube** (@BoyleCountyMedia) | CivicPlus Agenda Center | No | youtube.com/@BoyleCountyMedia | 2nd & 4th Tue |
| **Danville** | City commission | 17,236 | **YouTube** | CivicPlus/CivicEngage Agenda Center | No | youtube.com/@CityofDanvilleKentucky | 2nd & 4th Mon |
| **Montgomery County** | Fiscal court | 28,114 | **Facebook Live** (+ WMST radio) | montgomerycounty.ky.gov (PDF) | No | facebook.com/p/Montgomery-County-Fiscal-Court… | monthly |
| **Mount Sterling** | City council | 7,558 | **YouTube** (city-run) | mtsterling.ky.gov | No | youtube.com/channel/UCVCLFB_wB0fDfCvHh7lvZlA | 3rd Tue 7pm |

## Granicus-ready vs. needs-adapter vs. no-video

**🟢 Granicus-ready (near-zero-code onboard):** **None.** Lexington/LFUCG is the only Granicus instance in the region. The existing clip-ID-probe → `ViewPublisher.php` → WebVTT-caption ingest transfers to no neighbor.

**🟡 Needs adapter — YouTube (highest ROI; ~15 bodies):** Scott County, Georgetown, Woodford County (+FB), Clark County, Paris, Harrison County (+FB), Cynthiana, Madison County (+FB), Richmond (+Vimeo legacy), Berea, Franklin County (+FB), Lawrenceburg, Boyle County, Danville, Mount Sterling. *Good news:* the pipeline **already uses yt-dlp**, which pulls YouTube audio + metadata + auto-captions natively — a YouTube adapter is the smallest delta from the current code, and YouTube auto-captions can substitute for the Granicus WebVTT speaker track.

**🟠 Needs adapter — Facebook Live/Watch (harder; ~7 bodies):** Winchester, Bourbon County, Versailles, Midway, Frankfort, Mercer County, Montgomery County. Facebook has no clean public API for archived video; ingest means yt-dlp-against-FB or scraping, less reliable, often no captions, and frequently no durable per-meeting archive (just timeline "was live" posts).

**🟠 Needs adapter — Vimeo (1 body):** Nicholasville (videos on the shared BGADD regional Vimeo channel). yt-dlp supports Vimeo, but it's a shared regional channel — needs per-meeting disambiguation.

**🔴 No video found — out of scope for a video pipeline (6 bodies):** Jessamine County, Wilmore, Harrodsburg, Garrard County, Lancaster, Anderson County. PDF agendas/minutes only. A text-only "agenda + minutes" ingest could still feed RAG, but there's no transcript to extract facts from.

**Agenda-document layer (needed for the Table-of-Motions feature regardless of video):** CivicPlus/CivicEngage Agenda Center (Georgetown, Woodford Co, Clark Co, Winchester, Frankfort, Boyle Co, Danville) and CivicClerk (Paris — has a structured API at `parisky.api.civicclerk.com`) are the two structured agenda platforms worth writing fetchers for. Everyone else posts loose PDFs.

## Best first candidates

Since the Granicus-ready bucket is empty, "best first" = **large population × archived/structured YouTube video × a structured agenda portal** (the agenda portal matters because the pipeline's Table-of-Motions feature depends on machine-readable agenda packets):

1. **Madison County Fiscal Court** (92,701 — largest in the survey). Already runs a *searchable* archive table (agendas + minutes + video) on top of YouTube live+archive. Highest-value single onboard; their existing archive structure de-risks ingest.
2. **Scott County FC (57,155) + Georgetown (37,086)** — pair them for a whole-county onboard. Both YouTube; Scott is among the fastest-growing KY counties and Georgetown adds a CivicPlus Agenda Center for the motions layer.
3. **Boyle County FC (30,614) + Danville (17,236)** — cleanest "video + structured agendas" pairing in the set: both YouTube **and** CivicPlus/CivicEngage Agenda Centers, so both the transcript and the Table-of-Motions pipelines have a real source.
4. **Richmond** (34,585) — 7th-largest KY city and fastest-growing per 2026 census data; YouTube current archive + a Vimeo back-catalog (2021–2023) for historical depth.
5. **Paris** (10,171) — smaller, but the single cleanest *fully structured* stack: official YouTube channel for video **and** a CivicClerk portal with a JSON API (`parisky.api.civicclerk.com`) for agendas/minutes — the lowest-friction proof-of-concept for the agenda adapter.

**Strategic takeaway:** build the **YouTube ingest adapter first** (smallest delta from existing yt-dlp usage; unlocks ~15 bodies including the 3 largest counties), then a **CivicPlus Agenda Center + CivicClerk agenda fetcher** to preserve the Table-of-Motions feature. A Facebook adapter is a later, lower-ROI phase. Six small jurisdictions have no video and should be deprioritized or treated as text-only RAG sources.

*All platform classifications were verified by fetching the portals/channels; Granicus hosts were probed directly against the live `*.granicus.com` pattern. Unverified items are flagged inline (Cynthiana's exact YouTube channel handle; Berea's live-vs-archive split; the no-video bodies are "not-found" rather than confirmed-absent). Same-name out-of-state false positives are noted in the Granicus-host column.*

---

# 2. Per-Jurisdiction Fork & Onboarding Playbook

> Scope: how to stand up a 2nd, 3rd, … Nth Kentucky jurisdiction on the LFUCG meeting-pipeline codebase **without forking the code** — one Lightsail box per jurisdiction, differentiated only by config + per-tenant infra. Grounds in the implemented `config.py` / `jurisdictions/lfucg.toml` NOW-slice and the tenancy decision in `ARCHITECTURE_REVIEW.md` (Decision **A executed as C**: one box per jurisdiction, all built from one provisioning script + one codebase).

## 2.1 What "forking" means here

A new jurisdiction is **the same git repo, the same code, the same Docker/runtime image** — differentiated by exactly three things:

1. **`JURISDICTION=<slug>`** env var (selects the active config; default `lfucg`).
2. **`jurisdictions/<slug>.toml`** committed to the repo (Granicus host/views, clip range, taxonomy, collection name, site URL, and — see §2.2 — the infra/publication fields we still need to lift).
3. **Per-jurisdiction infrastructure**: its own Lightsail box, static IP, S3 bucket, CloudFront distribution, DNS pair (public + origin), scoped IAM user, and ChromaDB collection.

`config.py` already enforces the resolution order **env var > `jurisdictions/<slug>.toml` > built-in LFUCG default**, and the LFUCG defaults *are* the historical hard-codes, so behavior is byte-identical for LFUCG even with no TOML present (verified, 375 tests pass per the review). Onboarding is "drop a TOML, run the provisioner" — not "branch the repo."

**We do NOT git-fork per county.** Reasons:
- **Patching math.** Every security fix, parser hardening (the VTT/Table-of-Motions quirk fixes), prompt tweak, and dependency bump would have to be cherry-picked across N divergent forks. One codebase = `git pull` on N boxes (§2.8).
- **The seams are already data, not code.** The jurisdiction-specific logic is concentrated in a handful of methods that read `self.cfg` (Granicus URL builders, `fetch_date_from_listing`, `scrape_clip_metadata`'s body parser). These are config-parameterized, not branch-worthy.
- **Divergence is a liability, not a feature.** Counties don't want bespoke behavior; they want the same pipeline pointed at their video portal.

**When a code fork *would* be justified** (the honest exceptions):
- A jurisdiction on a **fundamentally different portal** (Legistar/CivicClerk/Swagit/YouTube) — but even this is an **adapter**, not a fork (§2.4). Only fork if the adapter abstraction itself can't express the new source (e.g., no per-meeting media URL at all, requiring a different processing model).
- A jurisdiction needing a **different data model** (e.g., bodies that don't map to "clip = one meeting video," like a state legislature with bill-centric structure). That's a different product.
- Short-lived **experimental divergence** you intend to merge back. Use a branch, not a permanent fork.

Rule of thumb: if the difference can be a TOML value or a new adapter class, it is **not** a fork.

## 2.2 Config schema extensions

`jurisdictions/lfucg.toml` today covers `[jurisdiction]`, `[granicus]`, `[taxonomy]`, `[storage]`, `[site]` — and `config.py` consumes all of them. But a 2nd jurisdiction trips over a set of values still **hard-coded outside `config.py`**. Here's the complete inventory, grouped by whether honoring config needs a code change.

**Already config-driven (consumed by `config.py` today — just set the TOML):**
| Field | Consumed by |
|---|---|
| `granicus_host`, `default_view_id`, `listing_view_fallbacks`, `first_clip_id` | `main.py` URL builders, `fetch_date_from_listing`, `probe_clips.py` |
| `body_patterns`, `body_acronyms` | `scrape_clip_metadata` body parser |
| `chroma_collection` | `rag/ingest.py` `COLLECTION_NAME` |
| `site_url` | `config.Jurisdiction.site_url` exists… **but is not yet read by `seo.py`/`mcp_server.py`** (they still read `LFUCG_SITE_URL` env or default) — see below |

**Still hard-coded — needs a code change to honor config:**
| Hard-coded value | Location(s) | Fix |
|---|---|---|
| `S3_BUCKET="s3://public-meetings"` | `deploy.sh:7` (literal); `ingest_cron.sh:29` + `sync_data_s3.sh` (env-default) | Read from env/config; `ingest_cron.sh` already takes `${S3_BUCKET:-…}`, but `deploy.sh` hard-codes the literal |
| `CLOUDFRONT_DISTRIBUTION_ID` | `deploy.sh:9` (env, no default), `ingest_cron.sh:30` (defaults to `E8OIXOXDRETLZ`) | Already env-driven; just drop the LFUCG default in `ingest_cron.sh` |
| `editor@lexingtonky.news` | `seo.py` ×4 (3 disclosure blocks + operator line) | Wire to `config.editor_email` |
| `"LFUCG Meeting Archive"` | `seo.py` (news sitemap `<news:name>`, llms.txt H1, llms-full.txt H1) | Wire to `config.publication_name` |
| `"Lexington-Fayette Urban County Government"` prose | `seo.py` (llms.txt/llms-full blurbs), `rag/prompts.py` ×3, `rag/mcp_server.py` ×6, `rag/server.py` FastAPI title | Wire to `config.name` (already in config!) |
| `"Operated by Paul Oliva…"` | `seo.py:585` | Wire to `config.operator_name` |
| `LFUCG_SITE_URL` default `https://meetings.lexingtonky.news` | `seo.py:338`, `rag/mcp_server.py:48` | Prefer `config.site_url`, keep env as override |
| `FEEDS_WEBHOOK_URL` / `FEEDS_API_TOKEN` | `.env` + `ingest_cron.sh:85-90` | Move webhook URL to config; token stays a secret in `.env` |
| Public domain / origin hostname | `Caddyfile` (`meetings-origin.lexingtonky.news`), CloudFront config | Caddy is rendered per-box (§2.5); domain belongs in config for the provisioner |
| systemd unit name `lfucg-rag`, `RAG_SERVICE` | unit file + `ingest_cron.sh` | Render unit name from slug: `<slug>-rag` |
| `LFUCG_OUTPUT_DIR` env name | everywhere | Cosmetic; keep the env name for back-compat, value is per-box |

**Proposed extended TOML** (`jurisdictions/<slug>.toml`):

```toml
[jurisdiction]
slug = "lexington"            # was "lfucg"; unique, dns-safe, used for unit/collection names
name = "Lexington-Fayette Urban County Government"   # already in config; wire prose to it

[granicus]
host = "lfucg.granicus.com"
default_view_id = 14
listing_view_fallbacks = [14, 9, 2, 4, 5, 6, 7, 8, 10, 13, 16, 17]
first_clip_id = 6669

[source]                       # NEW — for the §2.4 adapter abstraction
type = "granicus"             # "granicus" | "legistar" | "civicclerk" | "swagit" | "youtube"
# adapter-specific keys live under the adapter's own subtable, e.g. [source.legistar]

[taxonomy]
body_patterns = ["WQFB", "CAC", "LFUCG", "Council", "Commission", "Board", "Committee"]
body_acronyms = ["WQFB", "CAC", "LFUCG"]

[storage]                      # NEW fields beyond chroma_collection
chroma_collection = "lfucg_meetings"
s3_bucket = "s3://public-meetings"          # was hard-coded in deploy.sh
cloudfront_dist_id = "E8OIXOXDRETLZ"        # was env-only / LFUCG-defaulted

[site]
site_url = "https://meetings.lexingtonky.news"     # public CloudFront domain
origin_hostname = "meetings-origin.lexingtonky.news" # Caddy/Lightsail origin — NEW

[publication]                  # NEW — the seo.py / prompts identity block
publication_name = "LFUCG Meeting Archive"
operator_name = "Paul Oliva"
editor_email = "editor@lexingtonky.news"

[integrations]                 # NEW — optional downstream wiring
feeds_webhook_url = "https://feeds.lexingtonky.news/api/ingest/lfucg-meeting-archive"
# feeds_api_token stays in .env (secret), referenced by name only

[elected_reps]                 # NEW — optional, FORWARD-LOOKING (see note)
# source = "lfucg_arcgis"      # nothing in fuzzy-potato consumes this today;
# url = "..."                  # reserved so RAG synthesis could later name council
                               # members. Do NOT block onboarding on it.
```

**Honesty flags:**
- `[source]` and `[elected_reps]` are **not consumed yet** — they're schema reservations for §2.4 and a future enhancement. Don't let them gate a Granicus county.
- `slug` doubles as the seed for derived names (`<slug>-rag` unit, `<slug>_meetings` collection if you template it, output dir). Keep it short + DNS-safe.
- Secrets (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `FEEDS_API_TOKEN`, `RELOAD_TOKEN`) **never** go in the TOML — they stay in per-box `.env`. The TOML is committed to the repo; treat it as public.

The cheapest path to "config-clean" is one PR that rewires `seo.py`, `rag/prompts.py`, `rag/mcp_server.py`, `rag/server.py`, and `deploy.sh` to read `get_config()` instead of literals — exactly the LATER multi-tenant items the review parked. **This is gating work for jurisdiction #2** even on Granicus, because otherwise county #2's pages would say "LFUCG" and email `editor@lexingtonky.news`.

## 2.3 Discovery sub-procedure for a new Granicus jurisdiction

Before writing the TOML you need four facts: the **Granicus host**, the **listing `view_id`(s)**, the **`first_clip_id`**, and the **body taxonomy**. All discoverable without writing code.

**(a) Granicus host.** Most KY local governments on Granicus use `<entity>.granicus.com`. Confirm by loading their "Meetings/Video" page and checking the player iframe `src`, or:
```bash
curl -sL "https://<city>.gov/meetings" | grep -oE '[a-z0-9-]+\.granicus\.com' | sort -u
```

**(b) Listing view IDs + fallbacks.** The listing page is `ViewPublisher.php?view_id=N`. Each meeting body lives on its own view. Sweep a small range and keep the ones that return clip links:
```bash
HOST=somecity.granicus.com
for v in $(seq 1 30); do
  n=$(curl -s "https://$HOST/ViewPublisher.php?view_id=$v" \
        | grep -oE 'clip_id=[0-9]+' | sort -u | wc -l)
  [ "$n" -gt 0 ] && echo "view_id=$v -> $n clips"
done
```
The view with the most clips is your `default_view_id` (busiest body = council). The rest, **most-trafficked first**, become `listing_view_fallbacks`.

**(c) `first_clip_id`.** Find the earliest clip so cold-start (§2.6) probes the full archive.
```bash
yt-dlp --no-download --print title \
  "https://$HOST/player/clip/100?view_id=$DEFAULT_VIEW&redirect=true"
JURISDICTION=<slug> uv run python probe_clips.py 1 8000
```
`probe_clips.py` now reads host/view from `config.get_config()` (the review fixed its latent hard-coded `view_id=14`), and stops after 5 consecutive 404s — so set `first_clip_id` to the **lowest** id in `available_clips.json`. Granicus IDs are global per host, not per view, so a single sweep covers all bodies.

**(d) Body taxonomy.** Pull ~30 titles and eyeball the recurring body names/acronyms:
```bash
JURISDICTION=<slug> uv run python probe_clips.py 1 500
python3 -c "import json;[print(c['title']) for c in json.load(open('lfucg_output/available_clips.json'))['clips'][:40]]"
```
Map into `body_patterns` (word-boundary, case-insensitive alternation) and `body_acronyms` (the ones to keep UPPERCASE).

## 2.4 Non-Granicus adapter abstraction (gating work before non-Granicus counties)

The review (#14) already names `granicus.py` as the highest-leverage extraction precisely because it's "the exact seam a non-Granicus portal would reimplement." Today that logic is ~10 methods on `LFUCGPipeline`. Define a minimal **`VideoSource`** protocol that those methods delegate to; main.py orchestrates (download → transcribe → summarize → ingest) and the *source* is swappable.

**The seam — methods that are 100% portal-specific** (`main.py`):
- `clip_url` / `agenda_url` / `minutes_url` — URL construction
- `get_clip_title` — yt-dlp title fetch
- `scrape_available_clips` / `fetch_date_from_listing` — `ViewPublisher.php` scraping
- `download_audio` — `yt-dlp -x` against the player URL
- `download_agenda` / `download_minutes` — `AgendaViewer.php` / `MinutesViewer.php`
- `fetch_captions` — `yt-dlp --write-subs` VTT pull
- `scrape_clip_metadata`'s **date+body** logic — partly portal (date source), partly taxonomy (body parse, already config)

**Everything downstream is portal-agnostic and STAYS in the pipeline:** `transcribe_audio` (Whisper), `summary_v2`, `table_of_motions`, `granicus_captions` VTT *parsing* (the parse is reusable; only the *fetch* is portal-specific), `rag/*`, `seo.py`.

**Proposed interface** (`sources/base.py`):

```python
from typing import Protocol, Optional
from pathlib import Path
from dataclasses import dataclass

@dataclass
class MeetingRef:
    clip_id: str          # stable per-source id (Granicus clip_id; Legistar EventId; YouTube videoId)
    title: Optional[str]
    date: Optional[str]   # ISO YYYY-MM-DD if the listing exposes it
    body: Optional[str]   # meeting body if the listing exposes it

class VideoSource(Protocol):
    """Everything jurisdiction/portal-specific. main.py calls ONLY these."""
    def list_meetings(self) -> list[MeetingRef]: ...
        # replaces scrape_available_clips + fetch_date_from_listing
    def get_metadata(self, ref: MeetingRef) -> dict: ...
        # replaces get_clip_title + scrape_clip_metadata's date/source half
        # (body taxonomy parse stays shared, fed by config)
    def download_audio(self, ref: MeetingRef, dest_dir: Path) -> Optional[Path]: ...
    def fetch_captions(self, ref: MeetingRef, dest_dir: Path) -> Optional[Path]: ...
        # returns a WebVTT file or None; parsing stays in granicus_captions.py
    def download_agenda(self, ref: MeetingRef, dest_dir: Path) -> dict: ...
    def download_minutes(self, ref: MeetingRef, dest_dir: Path) -> dict: ...
    def canonical_url(self, ref: MeetingRef) -> str: ...
        # the public "source video" permalink (replaces clip_url for citation)
```

`GranicusSource` is the first impl — a near-verbatim lift of today's methods, reading `self.cfg`. `LFUCGPipeline.__init__` picks the adapter from `config.source.type` (`"granicus"` default). New portals become new files:
- **`YouTubeSource`** — channel/playlist enumeration via yt-dlp; no agenda/minutes (return `{}`), captions via `--write-auto-subs`. **(Per §1, this is the first adapter to build — it unlocks the bulk of the region.)**
- **`LegistarSource`** — Legistar Web API (JSON: `/v1/<client>/Events`, `EventInSiteURL`, `EventAgendaFile`/`EventMinutesFile`); audio/video via the linked media player.
- **`CivicClerkSource` / `CivicPlusSource` / `SwagitSource`** — their respective listing JSON/HTML + media endpoints.

**Mark as gating:** this extraction is **prerequisite work before the first non-Granicus county**, and should be **test-backed**. The config NOW-slice was the down-payment that makes it cheap. Sequence: (1) ship the `seo.py`/prompts config rewire (§2.2) for *any* 2nd county; (2) extract `GranicusSource` behind `VideoSource`; (3) add the `YouTubeSource` adapter (the first real non-Granicus need); (4) add a structured-agenda fetcher for the Table-of-Motions feature.

## 2.5 Repeatable infra provisioning — `provision-jurisdiction.sh <slug>`

One parameterized script stands up a box from nothing. It reads `jurisdictions/<slug>.toml` for the names/IDs and prompts for secrets. Outline (**[once]** = create-if-absent, idempotent-safe to re-run; **[idem]** = naturally idempotent):

```
provision-jurisdiction.sh <slug>
  load jurisdictions/<slug>.toml  →  $SLUG $SITE_URL $ORIGIN_HOST $S3_BUCKET ...

  1. [once] Lightsail instance  `<slug>-meetings`  (Ubuntu 22.04, 4GB/2vCPU/80GB, us-east-1)
  2. [once] Static IP, attach to instance
  3. [once] Lightsail firewall: open 22, 80, 443
  4. [once] S3 bucket  (from [storage].s3_bucket)  + public-read policy for /data,/assets
  5. [once] CloudFront distribution:
             default behavior → S3 (SPA)
             /api/* behavior  → custom origin = [site].origin_hostname (HTTPS-only)
           → capture the new dist id, write back to the TOML's cloudfront_dist_id
  6. [once] Scoped IAM user  `<slug>-box`  + policy: s3:* on THIS bucket only,
             cloudfront:CreateInvalidation on THIS dist only  → access keys to .env
  7. [once] Cloudflare DNS (zone, via API):
             A  <slug>-meetings.<zone> (public, proxied)  -> CloudFront
             A  <slug>-origin.<zone>    (origin, DNS-only) -> static IP
  8. [idem] SSH in; apt deps (ffmpeg tesseract-ocr poppler-utils git curl), uv, AWS CLI,
             Caddy, 4GB swap, timezone, Python 3.11 pin  (verbatim from SETUP.md §2)
  9. [once] Generate read-only deploy key, add as repo deploy key, git clone to
             /opt/fuzzy-potato;  [idem] uv sync --extra rag
 10. [idem] Render + install .env  (JURISDICTION=<slug>, secrets, S3/CF, RELOAD_TOKEN)
 11. [idem] Render systemd unit  `<slug>-rag.service`  from template (unit name,
             EnvironmentFile, MemoryHigh=3G) → enable --now
 12. [idem] Render Caddyfile for [site].origin_hostname → reload caddy
 13. [idem] sudoers NOPASSWD for `systemctl restart <slug>-rag`
 14. [idem] Install crontab (lean 6h ingest + weekly backfill), paths templated, flock-guarded
 15. → hand off to COLD-START (§2.6); a new county has NO seed
```

**Idempotency posture:** AWS resource *creation* (1–7, 9-key) is **[once]** — guard with `aws ... describe || create` so a re-run repairs a half-finished box instead of duplicating (Lightsail will happily make a second instance). Everything **on-box** (8, 10–14) is **[idem]**. Capturing the CloudFront dist id back into the TOML (step 5) makes the run self-documenting.

**Deliberately NOT scripted** (one-time human steps): the Cloudflare WAF crawler allow-list (shared zone rule), and cross-repo wiring (paulBot `!ask`, feeds civic-memory widget, master `~/lt/CLAUDE.md`). These are *integration*, not *provisioning*.

## 2.6 Cold-start vs seeded

LFUCG was **seeded** — the box pulled an existing ChromaDB + search.db out of the live ECR image and `clips/` from S3, **zero re-embedding cost** (SETUP.md §4). **A new county has none of that.** Its first run backfills the *entire* archive from the portal.

**Cold-start procedure:**
```bash
cd /opt/fuzzy-potato && export JURISDICTION=<slug>
# 1. Probe the full ID range (Granicus) / enumerate the channel (YouTube)
uv run python probe_clips.py 1 8000
# 2. Batched scrape+process; resumable via state.json; audio OFF to save disk
nohup uv run python main.py --scrape --rag --no-audio --max 9999 \
  > /var/log/<slug>-coldstart.log 2>&1 &
tail -f /var/log/<slug>-coldstart.log
# 3. One-time sweeps
uv run python main.py --backfill-tables-of-motions
uv run python main.py --upgrade-summaries --max 9999
# 4. Build search.db once, sync to S3, deploy SPA, invalidate CF
uv run python main.py --build-search-db
bash deploy/lightsail/sync_data_s3.sh
CLOUDFRONT_DISTRIBUTION_ID=<dist> ./deploy.sh
```

**Cost & time per ~1000 clips** (Whisper-dominated, $0.006/audio-min):
- **Whisper:** ~60–120 audio-min/clip → **~$0.40–0.75/clip** (long 3hr council meetings ~$1.00 each).
- **Summary v2 (GPT-4o + Claude Sonnet):** ~$0.15/clip. **Topics + embeddings:** pennies.
- **Total: ~$0.55–0.95/clip → ~$550–950 per 1,000 clips.** LFUCG-scale (~2,763 clips) would be ~$1.5–2.6k if it weren't seeded.
- **Wall-clock:** ~3–8 min/clip serial → ~50–130 hours (2–5 days) per 1,000 clips on one box.

**Throttling / safety:** run in `nohup`/`tmux`, chunked (`state.json` makes it resumable); **keep the 6h ingest cron DISABLED until cold-start finishes** (don't let lean + bulk fight over `search.db`/Chroma); mind the 80GB disk (`--no-audio` is important); set the rate limiter / WAF **before** flipping public DNS (once `/api/ask` is reachable it's a cost-attack surface). Consider a `--max N` nightly ramp to spread API spend.

## 2.7 Validation checklist + rollback (per jurisdiction)

**Validation (after cold-start, before flipping public DNS, then again after):**
```bash
SLUG=<slug>; ORIGIN=<slug>-origin.<zone>; PUB=<slug>-meetings.<zone>
curl -s localhost:8000/api/health
curl -s https://$ORIGIN/api/health
curl -s -X POST https://$ORIGIN/api/search -H 'Content-Type: application/json' -d '{"q":"budget","limit":3}' | head
curl -s -X POST https://$ORIGIN/api/ask    -H 'Content-Type: application/json' -d '{"question":"What did the council vote on most recently?"}' | head
curl -s -X POST https://$PUB/api/mcp/ -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' -H 'MCP-Protocol-Version: 2025-06-18' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}'
curl -sI https://$PUB/ | grep -i '200\|content-type'
curl -s https://$PUB/llms.txt | head -c 80          # must say <new publication_name>, NOT "LFUCG"
```
Checklist: `/api/health` 200 local+origin · search/ask/facets/related return data · MCP `initialize` returns a session · SPA loads, `clip.md` served as `text/markdown` · **`llms.txt`/`skill.md`/disclosures show the new jurisdiction (catches the §2.2 hard-code leak)** · ChromaDB collection == `config.chroma_collection` (no cross-tenant bleed) · cron enabled only after cold-start · feeds webhook points at the new ingest path.

**Rollback (per jurisdiction, isolated — blast radius is one city):**
- **Bad ingest / corrupt index:** `search.db` rebuilds from `clips/` in ~30s; `chroma_db` re-derives via `--rebuild-rag`. `clips/` is the only irreplaceable layer (mirror nightly to a versioned S3 bucket).
- **Bad code deploy:** `git reset --hard <prev>` + `uv sync` + `systemctl restart <slug>-rag` (bare systemd → checkout, not image pull).
- **Bad config:** revert the TOML, restart. A malformed TOML degrades to LFUCG defaults — **catch in validation**, because a 2nd county silently serving LFUCG host/views is a real failure mode.

**Teardown:** reverse of provisioning — remove cron; stop+disable `<slug>-rag`+Caddy; remove DNS (public+origin) + WAF entry; delete CloudFront + S3 (archive `clips/` first); delete IAM user+keys + deploy key; delete Lightsail instance + static IP; remove the TOML. Each resource is tenant-scoped, so teardown can't touch another county.

## 2.8 Maintenance at scale

**Patching N boxes from one codebase** — `git pull && uv sync && systemctl restart <slug>-rag`, as a `for` loop over an SSH inventory, canary-first:
```bash
for h in $(cat boxes.txt); do
  ssh "$h" 'cd /opt/fuzzy-potato && git pull --ff-only && ~/.local/bin/uv sync --extra rag && \
            sudo systemctl restart "$(basename $PWD)"-rag && curl -fsS localhost:8000/api/health'
done
```
**Config drift:** TOMLs are committed (version-controlled, PR-reviewable) — that's the anti-drift mechanism. The risk is on-box `.env`; audit by diffing each box's effective `get_config()` against the committed TOML.
**Monitoring:** wire each box's `/api/health` into the `lt-ops` `rag_health` check (one per jurisdiction); alert on 5xx, restart-looping (OOM flap), cron last-success age, and OpenAI/Anthropic spend anomalies (cost-attack; namespace the rate limiter per tenant once N>1).
**Per-tenant ChromaDB memory reality — the load-bearing fact:** ~5GB Chroma for the LFUCG archive, HNSW mmapped — **one tenant fits a 4GB box, two do not.** This is *the* reason for one-box-per-jurisdiction (a capacity ceiling, not a preference). **Don't attempt multi-tenant-per-box** until the embedding footprint shrinks (review #13: 512-dim truncation → ~3× smaller, a re-embed not a re-architecture → *then* evaluate sqlite-vec). Explicitly **not** to be done during onboarding.

---

# 3. Rollout Strategy, Cost & Operations

> **Reconciliation note (added in assembly):** this section's §3 sequencing framework was written treating "Granicus-readiness" as the Phase-1 gate. The survey (§1) shows **no neighbor is Granicus**, so Phase 1 is superseded by the engineering work in §0/§2.4 (config rewire → `GranicusSource` extraction → `YouTubeSource` adapter). The scoring framework, cost model, branding, and ops guidance below all still hold — read "Granicus-ready" as "ingestable by an existing adapter," which after the YouTube adapter ships means the ~15 YouTube bodies.

This section assumes the architecture already decided in `deploy/lightsail/ARCHITECTURE_REVIEW.md`: **one Lightsail box per jurisdiction** (decision "A executed as C" — identical image + provisioning script, differing only by a `jurisdictions/<slug>.toml` + DNS). The 5GB-Chroma-per-tenant footprint makes co-tenanting on a 4GB box infeasible, so cost scales honestly per data footprint and blast radius is one city. Everything below builds on that.

### 1. Per-Jurisdiction Run Cost

#### Steady-state monthly (one jurisdiction, ongoing weekly ingest)

| Line item | Small county (~3 clips/wk) | Mid/high county (~8 clips/wk) | Notes |
|---|---|---|---|
| Lightsail box (4GB / 2vCPU / 80GB) | $24.00 | $24.00 | Flat. Plan includes 4TB outbound transfer — civic traffic never approaches this, so transfer overage = $0. |
| S3 storage (per-clip txt/json/pdf/md/vtt; **no audio**, no chroma, no search.db) | ~$0.05 | ~$0.15 | `--no-audio` + the lean `sync_data_s3.sh` excludes mp3/chroma/search.db. A small archive is <1GB; Fayette-scale ~3–4GB. $0.023/GB-mo. |
| S3 requests (delta PUTs via `--size-only`, GETs served from CDN edge) | ~$0.05 | ~$0.10 | Pennies. |
| CloudFront (no fixed fee; transfer + invalidations) | ~$0.50 | ~$1.50 | `/data/*` invalidation fires only when new clips land (~tens/mo) — within the 1,000/mo free tier. Transfer-out a few GB/mo. |
| DNS (subdomain under one shared zone) | ~$0.00 | ~$0.00 | See §4 — umbrella zone, not per-county zones. |
| **Infra subtotal** | **~$24.65** | **~$25.75** | Box dominates (~95%). |
| LLM — ongoing ingest (~13 clips/mo vs ~35 clips/mo) | ~$6 | ~$16 | Blended ~$0.45/clip (see below). VTT coverage and summary-model choice swing this materially. |
| **Steady-state total** | **~$30–35/mo** | **~$40–55/mo** | |

**Per-clip LLM breakdown** (the only variable cost):
- **Whisper transcription** — $0.006/min × ~75–90 min meeting ≈ **$0.45–0.54/clip**, *but $0 when a Granicus VTT caption track exists* (already a first-class feature: VTT-as-placeholder or VTT-speaker enrichment). This is the single largest and most controllable line.
- **Two-pass summary** (GPT-4o extract + Claude Sonnet narrate) — **$0.15/clip**.
- **Topics** (gpt-4o-mini) — ~$0.01/clip.
- **Embeddings** (`text-embedding-3-small`, ~15K tokens/meeting) — ~$0.0003/clip, negligible.

So a clip costs **~$0.16 with VTT, ~$0.61–0.70 without, ~$0.03 with VTT + summary downshift**. Blended at ~50% VTT availability → **~$0.40–0.45/clip**.

#### One-time cold-start backfill (process the entire back-catalog once)

The dollar cost scales linearly with archive size; the *real* constraint is wall-clock (downloading + transcribing thousands of clips takes days on one box). Spin up a temporary larger Lightsail box, or run the box hot for a week, for the initial sweep.

| Archive size | Optimistic (VTT + downshift, ~$0.20/clip) | Typical blended (~$0.45/clip) | Worst case (all-Whisper, full summary, ~$0.70/clip) |
|---|---|---|---|
| 500 clips | ~$100 | ~$225 | ~$350 |
| 1,000 clips | ~$200 | ~$450 | ~$700 |
| 5,000 clips (Fayette-scale outlier) | ~$1,000 | ~$2,250 | ~$3,500 |

**Cost-deferral trick:** stand the archive up immediately as **VTT-placeholder transcripts** (searchable + RAG-ingestible, $0 Whisper) and only backfill Whisper for clips that lack captions or that draw traffic. Most neighboring counties have far shallower archives than Fayette, so a realistic per-county cold start is **$200–700**, not thousands.

### 2. Fleet Cost Model

| Fleet size | Infra (N × ~$25) | LLM steady (mix of small/mid) | **Steady-state monthly** | One-time cold-start (amortized over rollout) |
|---|---|---|---|---|
| 5 jurisdictions | ~$125 | ~$40 | **~$165/mo** | ~$1.5–4K total (one-time) |
| 10 jurisdictions | ~$250 | ~$90 | **~$340/mo** | ~$3–8K total |
| 20 jurisdictions | ~$500 | ~$200 | **~$700/mo** | ~$6–14K total (Fayette's $2–3K is the single biggest chunk) |

**Where the curve bends:** it doesn't, by design — one box per jurisdiction means infra is **strictly linear** with no economies of scale. The only structural bend available is the backlog item **#13 (truncate embeddings to 512-dim → ~3× smaller Chroma → multi-tenant-per-box becomes feasible on a 16GB box)**, which would collapse the N×$24 line into a handful of shared boxes. That's deferred until the fleet is large enough to justify the re-embed + correctness risk; below ~20 tenants the simplicity of one-box-per-city is worth more than the savings.

**Infra vs. LLM split:** at steady state, **infra is 70–85%** of monthly spend (the box dominates low-volume counties). LLM is small and predictable in steady state but **spikes hard at each onboarding** — a single Fayette-scale backfill ($2–3K) exceeds a *full year* of that box's infra. So model fleet cost as **flat infra + a one-time LLM bolus per onboard**, not a smooth ramp.

**Cheapest levers, ranked by impact:**
1. **Skip Whisper where Granicus VTT exists** (already built) — the biggest single lever; kills the largest per-clip cost. Prioritize counties with good closed-caption coverage.
2. **Summary model downshift for low-traffic counties** — `--summary-model gpt-4o-mini` drops the $0.15 two-pass to ~$0.02. Fine for sleepy boards nobody reads deeply; keep the full GPT-4o+Claude pass for flagship/high-newsworthiness bodies.
3. **VTT-placeholder cold start** — defer/skip Whisper for the long tail of old, low-value clips; transcribe on demand.
4. **Embedding dimension truncation (512-dim)** — future structural lever (#13); enables multi-tenant consolidation, the only thing that breaks infra linearity.
5. **Right-size very small archives** — a county with <1,000 clips has <2GB Chroma and could run the $12/mo 2GB box; keep the 4GB default unless the box proves stable, because the pipeline (ffmpeg/Whisper/OCR) is bursty and the 4GB+swap headroom is what keeps it from OOM-killing the API.

### 3. Sequencing / Prioritization Framework

Score each candidate 1–5 on five axes, weighted. **Granicus-readiness is gating**, not just weighted: a non-Granicus jurisdiction is *blocked* from Phases 1–2 until the portal adapter exists (§ phase 3).

| Axis | Weight | 1 (low) → 5 (high) |
|---|---|---|
| **Granicus-readiness** | ×3 (gating) | 5 = standard Granicus host + view IDs → pure `<slug>.toml`, zero code. 1 = non-Granicus portal (CivicClerk/Swagit/Vimeo/YouTube-only) → needs an adapter first. |
| **Population / newsworthiness** | ×2 | Audience size, civic activity, presence/absence of existing local coverage (a coverage desert scores higher — more strategic value). |
| **Meeting cadence / volume** | ×1 | More clips/week = more product value (slight steady-state cost penalty, see §1). |
| **Archive depth (cold-start cost)** | ×1 (inverse) | Shallow archive = cheap + fast to launch → scores high. A 5,000-clip back-catalog scores low (expensive bolus). |
| **Strategic / regional value** | ×2 | Proximity to Lexington (shared readership, cross-promo via LexBot ticker + feeds + situation map), regional importance, demonstration value. |

The platform survey (another teammate) fills **Platform**, **Granicus-ready?**, **est. archive size**, and **cadence**; the framework then computes a score and phase. Candidate scorecard template:

| Jurisdiction | Platform | Granicus-ready? | Pop. | Est. archive | Cadence | Weighted score | Phase |
|---|---|---|---|---|---|---|---|
| _(survey fills these)_ | | | | | | _(computed)_ | |

**Phased rollout:**
- **Phase 0 — done.** LFUCG reference tenant live; multi-tenant config (`config.py` + `jurisdictions/lfucg.toml`) landed; behavior byte-identical.
- **Phase 1 — 2–3 Granicus-ready neighbors** (highest Granicus-readiness × population, shallowest archives). These are **pure config** — a TOML + DNS, no code. Purpose: prove the second/third tenant onboards cleanly and shake out the still-hard-coded bits (deploy.sh's `public-meetings`/`E8OIXOXDRETLZ`, `seo.py`'s `editor@…`, per-tenant rate-limit namespacing — all flagged as "remaining MT work" in the architecture review). Do this *before* automating, so the automation reflects reality.
- **Phase 2 — remaining Granicus jurisdictions.** By now provisioning is one-command and onboarding cadence accelerates. **Gate: the centralized health dashboard + fleet-deploy must exist before this phase (N>5, see §6).**
- **Phase 3 — non-Granicus jurisdictions.** Requires first extracting `granicus.py` (the portal seam, backlog #14) into a portal-adapter abstraction, then building the first concrete adapter (whatever the survey shows is most common — CivicClerk / Swagit / direct YouTube). Per-county engineering cost is real here, so admit only high-score targets that justify the adapter build; one adapter then unlocks every county on that platform.

### 4. Domain & Branding Scheme

| Option | Convention | Pros | Cons |
|---|---|---|---|
| **A — subdomains on the Lexington zone** | `meetings-<county>.lexingtonky.news` | Reuses the existing Cloudflare zone; zero new domains. | Brands every county "Lexington"; alienates Frankfort/Georgetown/Nicholasville residents; subdomain sprawl on the editorial zone. |
| **B — per-jurisdiction zones** | `<county>ky.news` each | Strong independent local identity per town. | $/domain × N; N× DNS/cert/WAF config; brand fragmentation; heaviest ops. |
| **C — umbrella civic-memory network** ⭐ | `<county>.civicmemory.news` (or `bluegrasscivic.news`) | One zone, one wildcard-TLS story, scalable DNS; **neutral network brand** that isn't Lexington-centric; maps 1:1 to the per-box `config.site_url`. | New umbrella brand to establish; less "local" feel than a town-named site. |
| **D — per-town "Times" sub-brands** | "The Scott County Times", etc. | Maximum local identity; editorial continuity with The Lexington Times. | Implies *human editorial* commitment per town; heavy brand/content overhead; conflates the cheap archive utility with an expensive editorial product. |

**Recommendation: C as the default for the machine/archive surface, with D layered on selectively.** The meeting-archive product is *civic infrastructure*, not editorial voice — a neutral network brand (`civicmemory.news`, "Bluegrass Civic Memory") scales cleanly and is honest about what it is (an AI-built public-record index). Mechanics mirror the existing LFUCG split:
- `<county>.civicmemory.news` → CloudFront (SPA + `/api/*` behavior).
- `<county>-origin.civicmemory.news` → Lightsail static IP (Caddy HTTP-01 TLS), same as today's `meetings-origin.lexingtonky.news`.
- `config.site_url` per TOML drives all the SEO/llms.txt/MCP-discovery URLs.

Keep **`meetings.lexingtonky.news` as a permanent alias for LFUCG** (it's the flagship, and breaking existing MCP-discovery + paulBot/feeds links is not worth the tidiness). Reserve the **"<Town> Times" sub-brand (D) only where a real human editorial surface exists** (as lexingtonky.news is for Fayette) — let the cheap-to-run archive sit under the umbrella, and brand a separate editorial product on top only if/when one is built for that town. This decouples the $25/mo archive from the expensive editorial brand.

### 5. Governance, Legal & Content

- **Open-records posture is favorable.** Meeting video, agendas, and minutes are public records under KY Open Records (KRS 61.870–.884), and the meetings themselves are public under KY Open Meetings (KRS 61.800–.850). Granicus *is* the government's own publication channel. Indexing and republishing public government proceedings is core protected activity — this is the legal foundation of the whole network.
- **Reproduction / fair-use rules already baked in — keep them per-tenant.** Title + ≤200-char summary, otherwise paraphrase + cite; the AI-generated summaries are *transformative* (structured extraction + narrative); every clip links back to the canonical Granicus video at the exact timestamp. The transcript-source disclosure aside ("Closed-caption placeholder…", AI-origin disclosure) carries forward unchanged. These are content rules, not per-county config — they ship in the code.
- **Defamation surface grows with coverage** and is the chief content risk. AI summaries can mis-state a vote, mis-attribute a public comment, or hallucinate. Mitigations, in order of strength: (a) the **Table-of-Motions feature** replaces transcript-derived motions with the *official printed record* — extend that "official wins" discipline wherever an authoritative source exists; (b) the always-present **link to the source video timestamp** makes every claim independently verifiable; (c) **AI-origin disclosure** sets reader expectation; (d) public officials acting officially face the actual-malice standard (high bar), **but private citizens giving public comment are more exposed** — apply summary discretion / consider not over-amplifying named private individuals. **Roster accuracy is both a defamation and a credibility risk:** a wrong council-member name attached to a vote is the most likely concrete harm.
- **Takedown / correction handling.** Stand up a documented, per-network contact path (note `seo.py` hard-codes `editor@…` in ~4 places — must be generalized per tenant). Corrections are cheap by construction: edit the clip artifact in `clips/` and re-ingest (Chroma + search.db both rebuild deterministically from `clips/`). Keep an audit trail. You generally cannot be compelled to remove *accurate* reporting of public records, but a clean, fast correction policy is reputational table stakes as coverage widens.
- **Per-county editorial config (onboarding checklist item).** Verify at onboarding and re-verify after each election: council-member roster, body taxonomy (`body_patterns` / `body_acronyms` in the TOML — `WQFB`/`CAC` are Lexington-specific), presiding officer, body names. Stale rosters after elections are a recurring maintenance item (the elected-rep / voting-precinct stack in `situationMonitor` is a candidate source of truth to sync against). **ToS check per platform:** confirm each portal's terms permit automated download (yt-dlp) and observe rate-limit politeness — flag this in the platform survey.

### 6. Operational Load at Scale

**The one-box-per-jurisdiction model is an operational asset, not just a cost choice — failure isolation is its main payoff:**
- A tenant's bursty pipeline (ffmpeg / Whisper / OCR) **can't OOM another tenant's API**.
- A corrupt `chroma_db` / `search.db` is *one city's* problem and rebuilds deterministically from `clips/` (search.db ~30s; chroma re-embeds for OpenAI $).
- A cost-attack on one `/api/ask` bills *one box* (note #16: on flat-rate hosting, hammering `/api/ask` is now a *cost* attack — keep the disk-persisted rate limiter on and namespace it per tenant).
- A bad deploy can be **canaried on one box** before fleet rollout.
- The SPOF is per-city and degrades only *enhancement* surfaces (paulBot `!ask`, feeds civic-memory widget), which already degrade gracefully on 5xx/timeout.

The cost of this model is **N× patching**, mitigated entirely by an identical image + provisioning automation.

**Monitoring / alerting.** Each box already exposes `/api/health` and runs systemd `Restart=always` with a soft `MemoryHigh=3G`. Extend the existing `lt-ops` `rag_health` check into a **per-tenant fleet sweep**. Alert on: API down / repeated restarts (crash loop, not a one-off restart), **ingest staleness** (no advance in `state.json`'s `last_processed_clip_id` *when clips were expected* — tune carefully, small counties legitimately go weeks with no new clips), disk >80% (11GB+growth on an 80GB box), OOM events, LLM key/quota errors, and **cost anomalies** (a cron billing spike = scraper attack).

**Cron fleet.** Each box self-runs its own crontab — lean incremental ingest every 6h on weekdays (02/08/14/20 ET) and a heavy archive-wide sweep weekly (Sun 03:00 ET). There's deliberately **no central scheduler** (failure-isolated by design), but you need central *visibility*: ship each box's `/var/log/lfucg-ingest.log` + a per-run heartbeat to a central sink (CloudWatch Logs or a tiny heartbeat ping) so "did every box's cron succeed last night" is answerable from one place.

**Span of control & the automation gate.** One operator can run **~5 boxes by hand** (SSH, tail logs, manual restart). Beyond that it does not scale without automation. **Must-build before N>5 (the Phase-2 gate):**
1. **One-command provision** — parametrize `SETUP.md` into a `provision.sh` taking a slug + TOML + DNS (currently ~8 manual steps including the Docker-based data seed).
2. **Fleet-wide deploy** — `git pull && uv sync && reload` across all boxes (a for-loop over SSH, Ansible, or SSM) — today deploy is per-box and partly hard-coded.
3. **Centralized health dashboard** — one page hitting all N `/api/health` + last-ingest timestamp + index freshness.
4. **Templatize `deploy.sh` per jurisdiction** — bucket / CloudFront dist / domain are hard-coded `public-meetings` / `E8OIXOXDRETLZ` / `meetings.lexingtonky.news` (flagged as remaining MT work).
5. **Per-tenant rate-limit namespacing** (#16) and **per-tenant takedown contact** (`seo.py` `editor@…`).

With that automation in place, the boxes are identical, self-healing (systemd) and self-ingesting (cron), so a single part-time operator can realistically run **15–25 boxes** — the residual work is onboarding, post-election roster maintenance, and incident response, not babysitting. Without the automation, the ceiling is ~5.

---

# 4. Reconciled, Sequenced Roadmap

Tying §1 (no Granicus neighbors), §2 (the fork/adapter mechanics), and §3 (cost/sequencing) together:

**Phase 0 — DONE.** LFUCG reference tenant live on Lightsail; App Runner retired; multi-tenant config NOW-slice landed (`config.py` + `jurisdictions/lfucg.toml`), behavior byte-identical, 375 tests pass.

**Phase 1 — Engineering the second-tenant + non-Granicus path (no county onboards yet).**
1. **Config rewire** (§2.2): move LFUCG identity (publication name, editor email, operator, S3 bucket literal in `deploy.sh`, `LFUCG_SITE_URL`, body prose in `seo.py`/`rag/prompts.py`/`mcp_server.py`/`server.py`) into `config.py`/TOML. *Gating for any 2nd tenant.*
2. **Extract `GranicusSource` behind the `VideoSource` protocol** (§2.4), test-backed — proves the seam with zero behavior change for LFUCG.
3. **Build `YouTubeSource`** (§2.4 + §1) — the first real non-Granicus adapter; smallest delta (pipeline already uses yt-dlp); unlocks ~15 bodies.
4. **Build a structured-agenda fetcher** — CivicPlus Agenda Center + CivicClerk (Paris has a JSON API) — to keep the Table-of-Motions feature alive for YouTube counties.
5. **`provision-jurisdiction.sh`** (§2.5) — one-command box stand-up; required before N>~5 (strategy §6 automation gate).

**Phase 2 — First onboards (YouTube counties).** In priority order (§1 best candidates, scored via §3's framework):
1. **Madison County FC** (92,701; pre-structured archive) — flagship proof.
2. **Scott County FC + Georgetown** (paired; Georgetown adds CivicPlus agendas).
3. **Boyle County FC + Danville** (cleanest YouTube + CivicPlus pairing).
4. **Richmond** (YouTube + Vimeo back-catalog for depth).
5. **Paris** (fully structured: YouTube + CivicClerk JSON API).
Each is: write `<slug>.toml` → `provision-jurisdiction.sh` → cold-start backfill (§2.6, ~$200–700) → validate (§2.7) → flip DNS. Build the **centralized health dashboard + fleet-deploy loop** (strategy §6) before going past ~5 boxes.

**Phase 3 — Facebook + Vimeo + text-only (lower ROI, later).** A `FacebookSource` (yt-dlp/scrape, often caption-less, fragile archives) unlocks ~7 bodies; a Vimeo disambiguator handles Nicholasville's shared BGADD channel; the 6 no-video jurisdictions become text-only RAG (agenda/minutes ingest, no transcript) if pursued at all.

**Cross-cutting (any time):** register the umbrella brand/zone (`civicmemory.news` recommended, strategy §4); keep the per-tenant content/legal rules (fair-use, AI disclosure, roster accuracy, takedown path — strategy §5); namespace the rate limiter per tenant (cost-attack defense).

**One-line thesis:** the expansion is gated not on infrastructure (the one-box-per-jurisdiction model + provisioner handle that) but on a **YouTube ingest adapter** — because in the Bluegrass, Lexington's Granicus setup is the exception, not the rule.
