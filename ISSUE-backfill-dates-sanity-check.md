# Issue: `backfill_dates.py` writes hallucinated dates — add a sanity check

**Filed:** 2026-07-16 · **Priority:** medium (data already repaired; this is prevention)
**Owner:** unassigned · **Incident docs:** `~/lt/.report-data/centrepointe/fuzzy-potato-date-bug.md`

## What happened

On 2026-07-16, verification work for a Lexington Times CentrePointe investigation found **15 clips
in the LFUCG archive carrying wildly wrong dates**: 2008-era council meetings (Mayor Newberry
presiding) stamped with 2024–2026 dates. Examples: clip 600 (actually **2008-09-18**, per its own
official minutes) was stamped `2024-01-11`; clip 680 (actually 2008-12-09) was stamped `2026-06-16`.

The bad dates surfaced these clips in recency-sorted queries and led an external research brief to
falsely conclude CentrePointe was under council discussion in 2024–2026. The dates were corrected
by hand the same day (see "Repair procedure" below), but nothing prevents the same corruption from
being written again.

## Root cause

`backfill_dates.py` fills missing `date` fields in `lfucg_output/clips/<id>/metadata.json` from
five sources in priority order:

1. Granicus ViewPublisher listing (authoritative; **recent clips only**)
2. Granicus RSS feed (**last ~100 clips only**)
3. `extracted_facts.json` `meeting_info.date` — **LLM-extracted (GPT-4o)**
4. Transcript first 3000 chars (regex)
5. Linear interpolation from nearest dated clip-ID neighbors

Old clips (the low-600s band is 2008–2009 content) fall through sources 1–2 and land on
**source 3, which hallucinated plausible-looking modern dates**. Nothing validated the candidate
against the clip's position in the archive, so `2024-01-11` was written onto a clip sitting between
neighbors dated `2008-09-16` (clip 598) and `2008-10-07` (clip 615). All corrupted clips carried
`Last revised: 2026-06-17` — one backfill/reprocessing pass that day wrote the batch.

## The fix (spec)

### 1. Neighbor-window sanity check (required)

Clip IDs are roughly monotonic by meeting date. Before writing a candidate date from **any**
source (cheap to apply universally; sources 3–4 are the dangerous ones):

- Build a sorted list of `(clip_id, date)` for all clips that already have a date from a
  **trusted** source (Granicus listing/RSS, or previously validated).
- Locate the candidate clip's nearest dated neighbors by ID on each side.
- **Reject** the candidate if it falls outside `[prev_neighbor_date − WINDOW, next_neighbor_date + WINDOW]`
  with `WINDOW = 90 days` (generous; real gaps between adjacent IDs are days-to-weeks).
- On rejection: log loudly (`REJECTED source=llm clip=600 candidate=2024-01-11
  neighbors=598:2008-09-16 / 615:2008-10-07`), then fall through to source 5 (interpolation,
  already tagged "estimated") or leave the date missing. Never write a rejected candidate.

Edge cases: clips at the head/tail of the ID range have only one neighbor — use a wider one-sided
window (e.g. 1 year). A handful of IDs are genuinely out of order (late uploads); the 90-day window
absorbs the known real cases, but keep the threshold a named constant.

### 2. `--audit` mode (required)

A read-only pass over all existing `metadata.json` files applying the same neighbor check and
printing violations without writing anything. This is the regression guard: **it must report zero
violations on current prod data** (post-repair) and would have caught all 15 corrupted clips.
Wire it into `ingest_cron.sh` as a warning-only step if cheap.

### 3. Prefer the official-minutes date as a new high-priority source (optional, recommended)

The authoritative date for old clips is printed on page 1 of the official minutes, which are plain
PDFs behind Granicus viewers:

```
curl -sI "https://lfucg.granicus.com/MinutesViewer.php?view_id=14&clip_id=<ID>"
  → Location: /DocumentViewer.php?file=lfucg_<hash>.pdf&view=1
curl -s "https://lfucg.granicus.com/DocumentViewer.php?file=lfucg_<hash>.pdf&view=1" \
  -A "Mozilla/5.0" -o minutes.pdf     # plain UA required; fancy UA strings get an HTML shell
pdftotext -f 1 -l 1 minutes.pdf -     # date appears in the first ~5 lines
```

This was used on 2026-07-16 to pin clips 629 (Oct 23, 2008) and 646 (Nov 6, 2008) that had no
self-stated date in their narratives. Insert as source 2.5 (after RSS, before LLM). Rate-limit
politely; cache the resolved PDF hash in metadata.

## Test cases (real, from the incident)

Corrupted → correct (all 15, already repaired in prod; useful as fixtures):

| clip | bad date | true date | | clip | bad date | true date |
|---|---|---|---|---|---|---|
| 600 | 2024-01-11 | 2008-09-18 | | 649 | 2025-07-31 | 2008-11-11 |
| 602 | 2024-01-23 | 2008-09-23 | | 659 | 2025-10-23 | 2008-11-18 |
| 608 | 2024-04-11 | 2008-09-30 | | 662 | 2025-11-19 | 2008-11-20 |
| 621 | 2024-09-12 | 2008-10-14 | | 664 | 2025-12-04 | 2008-11-25 |
| 624 | 2024-10-14 | 2008-10-21 | | 669 | 2026-02-12 | 2008-12-02 |
| 629 | 2024-12-05 | 2008-10-23 | | 672 | 2026-03-12 | 2008-12-04 |
| 646 | 2025-06-24 | 2008-11-06 | | 679 | 2026-06-09 | 2008-12-09 |
| | | | | 680 | 2026-06-16 | 2008-12-09 |

**False-positive guards** (clips that look suspicious but are correctly dated — the check must NOT
flag them): 2048 (2011 — retrospective "Mayor Newberry had a growth plan" mention), 4020 (2016 —
"Mayor Newberry Portrait" agenda item), 6720/6734 (2026 — public commenter named **James Newberry**,
unrelated). Lesson: "Newberry appears in text" is not a date signal; only the neighbor window and
the minutes are.

## Repair procedure (if bad dates ever ship again)

On the `lfucg-meetings` box (`/opt/fuzzy-potato`, run as ubuntu, uv at `~/.local/bin/uv`):

1. Back up and edit `date` in `lfucg_output/clips/<id>/metadata.json`.
2. `uv run python main.py --generate-index` — regenerates `index.json`, per-clip `clip.md`, `search.db`.
3. `uv run python -m rag.ingest --clip <id>` per clip — ChromaDB re-ingest (delete-then-insert).
4. `POST /admin/reload` with `X-Reload-Token` from `.env`.
5. `bash deploy/lightsail/sync_data_s3.sh` — pushes `/data/*` to the `public-meetings` bucket.
6. CloudFront invalidation `/data/clips/<id>/*` + `/data/index.json` on **E8OIXOXDRETLZ**
   (⚠️ that's meetings.lexingtonky.news — `E28B7ORY9035LU` in `deploy-spa.sh` is the **Paris** box's
   distribution; box IAM can't invalidate, run from a workstation with admin creds).

## Acceptance criteria

- [ ] Neighbor-window check rejects all 15 fixture bad dates when replayed against pre-fix state.
- [ ] `--audit` reports zero violations on current prod data and doesn't flag 2048/4020/6720/6734.
- [ ] Rejected candidates are logged with source, candidate, and neighbor context; never written.
- [ ] Unit tests beside the script (pytest, `tests/`); hermetic — no live Granicus calls in default run.
- [ ] (If source 2.5 added) minutes-derived dates tagged "exact" in the log and covered by a
      fixture test using a saved minutes first-page text.
