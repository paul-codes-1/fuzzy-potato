# RAG Capacity Plan — post-backfill vector-store re-architecture

*2026-07-16. Status: RAG surface SUSPENDED (Caddy-gated) pending this plan.*

## Why we're here

The July 2026 local-Whisper backfill grew the archive from ~2,760 to ~3,650
clips (+887 transcripts, 1,630 h of audio). ChromaDB — an **in-RAM HNSW**
store — grew with it: 5.2 GB on disk (June) → 6.4+ GB, 192k+ chunks. The
serving process ballooned from ~4.8 GB RSS to 12.7 GB, and the box froze
twice in three days:

- **7/14** (8 GB box): uvicorn OOM → swap-thrash → full freeze, ~15 min
  outage. Fixed by upsizing to 16 GB + a systemd memory cap.
- **7/16** (16 GB box): repeated post-wave `/admin/reload`s leaked old
  Chroma allocations (glibc arenas never return); uvicorn crept to 12.7 GB
  → **global** OOM (cap was set too close to physical) → ~7 h outage.

Root problem: an in-RAM vector index whose size tracks archive growth,
serving on the same box as the pipeline. Every future jurisdiction and
every backfill makes it worse. RAM is not a plan.

## Current mitigations (in place)

- Caddy 503-gates `/api/ask|chat|related` + `/api/mcp` + `/mcp`
  (`Caddyfile.pre-rag-suspend` = instant rollback). **Keyword search,
  facets, suggest, all clip pages: unaffected** (SQLite FTS + static S3).
- Push-wave finalize no longer ingests into Chroma — clip ids queue in
  `pending_rag_ingest.txt` on the box for one bulk ingest post-rebuild.
- `lfucg-rag` memory cap 8G/10G (kill+3s-restart long before box freeze).
- Finalize restarts the unit instead of reloading (resets RSS each wave).

## Options

| # | Option | RSS effect | Cost | Effort | Notes |
|---|--------|-----------|------|--------|-------|
| 1 | Re-embed at 512 dims (`text-embedding-3-small` matryoshka) | vectors 3× smaller | ~$3–5 API | small code Δ + rebuild hours | negligible retrieval loss at top-K with rerank-free design |
| 2 | Disk-first store: **sqlite-vec** (or LanceDB) | RSS → 1–2 GB flat | $0 | ~1 day port of `rag/ingest.py` + `rag/query.py` | sqlite-vec matches the search.db ops posture: a file, no daemon, mmap'd |
| 3 | Prune/coarsen old-clip transcript chunks | −30–50% | $0 | small | lossy for exactly the history we just surfaced — last resort |
| 4 | 32 GB box | none (defers) | +$80/mo forever | zero | rents RAM for an unbounded growth curve; Paris repeats it |

## Recommendation: 1 + 2 together

Target: **steady-state RSS under 2 GB with full-archive coverage**, immune
to archive growth, same box, no new daemon.

Phase 1 (LFUCG box, ~1 day of work + rebuild time):
1. Port `rag/ingest.py` + `rag/query.py` to sqlite-vec, embedding at 512
   dims. Chroma code paths stay behind a flag until cutover verified.
2. Rebuild from `lfucg_output/clips/` (rebuildable by design) + drain
   `pending_rag_ingest.txt`.
3. Re-run the anti-hallucination eval suite (tests/test_query.py + the
   2026-06-11 grounding checks) against the new store.
4. Un-gate Caddy; watch RSS for a week (`systemctl status lfucg-rag`).

Phase 2: same port on the Paris box (smaller index, same code).

Rollback at any point: restore `Caddyfile.pre-rag-suspend`; the existing
`chroma_db/` stays on disk untouched until Phase 1 verifies.

## Related decision: summaries pipeline steady state

The July batch proved the hybrid: **local Qwen3-30B-A3B extraction (free,
~97% of clips) + Claude Haiku narration (~3–5¢/clip), with GPT-4o rescuing
the rare pathological mega-packet (~$0.15/clip, ~3% of clips)**.
Recommended steady state after the batch: keep the box's GPT-4o+Haiku cron
for the daily 1–3-clip trickle (≈$5–10/mo, no Mac dependency in prod);
reserve the Mac pipeline for bulk jobs (new jurisdictions, regens) — e.g.
the still-open pre-2025 summary regen (~$360 on GPT-4o) becomes a free
weekend Mac run.
