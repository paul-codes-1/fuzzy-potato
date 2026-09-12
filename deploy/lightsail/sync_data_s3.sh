#!/usr/bin/env bash
#
# Lean S3 sync of the per-clip archive + top-level index, sourced
# DIRECTLY from lfucg_output/ — no `npm run build`, no 11GB copy into
# frontend/dist/data/ (that copy only matters when the SPA *bundle*
# changes, which is a separate frontend code deploy via ../../deploy.sh).
#
# Mirrors the cache-control pools in ../../deploy.sh so CloudFront keeps
# serving the per-clip tree from edge. `--size-only` is used ONLY for the
# truly-immutable PDF pool: mutable files (extracted_facts.json, summary
# txt, clip.md, index.json) legitimately get rewritten in place, and a
# same-size rewrite would silently never sync under --size-only. The
# default time+size comparison uploads only files modified since their
# last upload, so cron runs still push just the deltas.
#
set -euo pipefail
export AWS_PAGER=""

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
S3_BUCKET="${S3_BUCKET:-s3://public-meetings}"
SRC="lfucg_output"
CF="${CLOUDFRONT_DISTRIBUTION_ID:-}"

# `--prerender-only`: ship just the pre-rendered per-clip HTML pages (used by
# deploy-spa.sh right after a new bundle lands, when only the page shells
# changed and the 11GB /data tree comparison would be wasted time).
PRERENDER_ONLY=0
if [ "${1:-}" = "--prerender-only" ]; then PRERENDER_ONLY=1; fi

if [ "$PRERENDER_ONLY" -eq 0 ]; then
# Per-clip PDFs (agendas + minutes): never change after processing -> 1y immutable.
aws s3 sync "$SRC/" "$S3_BUCKET/data/" --size-only \
  --exclude "*" --include "clips/*/*.pdf" \
  --cache-control "public, max-age=31536000, immutable"

# Per-clip Markdown alternates (clip.md): AI-agent discovery surface,
# regenerated when a clip's summary/facts change -> 1d fresh + 7d SWR.
aws s3 sync "$SRC/" "$S3_BUCKET/data/" \
  --exclude "*" --include "clips/*/clip.md" \
  --content-type "text/markdown; charset=utf-8" \
  --cache-control "public, max-age=86400, stale-while-revalidate=604800"

# Per-clip text + JSON + HTML (transcripts, summaries, agenda/minutes txt,
# metadata.json, extracted_facts.json): 1d fresh + 7d SWR.
aws s3 sync "$SRC/" "$S3_BUCKET/data/" \
  --exclude "*" \
  --include "clips/*/*.txt" \
  --include "clips/*/*.json" \
  --include "clips/*/*.html" \
  --cache-control "public, max-age=86400, stale-while-revalidate=604800"

# Top-level data (index.json, llms.txt, available_clips.json): mutates every
# run -> no-cache. Exclude the big local-only artifacts: chroma_db/, search.db,
# and vec.db (the sqlite-vec store) are served by the LOCAL RAG API now, NOT
# from S3, so there's no reason to ship them (search.db = 300MB, vec.db =
# 300MB+, chroma_db = 5.1GB). Also exclude raw media + per-clip files (handled
# above) and internal pipeline state (state.json / rag_state.json have no
# business on the public bucket).
#
# SECURITY: telemetry.db (hashed IPs + query text), vec.db (the live vector
# store), and any *.bak backup files must NEVER land on the public CloudFront
# /data/* path. The trailing `*` on the db globs also catches the sqlite WAL
# sidecars (-wal / -shm / -journal).
# prerender/ + prerender_state.json are the per-clip HTML pages (shipped to
# the bucket ROOT below, never under /data/) and their fingerprint ledger.
aws s3 sync "$SRC/" "$S3_BUCKET/data/" \
  --cache-control "no-cache" \
  --exclude "clips/*" \
  --exclude "chroma_db/*" \
  --exclude "search.db" \
  --exclude "state.json" \
  --exclude "rag_state.json" \
  --exclude "telemetry.db*" \
  --exclude "vec.db*" \
  --exclude "*.bak" \
  --exclude "prerender/*" \
  --exclude "prerender_state.json*" \
  --exclude "*.mp3" --exclude "*.mp4" --exclude "*.part" --exclude "*.ytdl"

# ---- SEO / agent artifacts (sitemap*.xml, robots.txt, llms*.txt, skill.md,
# .well-known/). generate_seo_artifacts() rewrites these into frontend/public/
# on every --generate-index run, but NOTHING shipped them: this script only
# ever synced lfucg_output/ -> /data/, and deploy-spa.sh only uploads
# dist/assets/ + dist/index.html. So the live sitemap froze at whatever a
# manual upload last left there (2026-06-03: 2,769 URLs / 12 weeks stale,
# caught 2026-08-25) while the box happily regenerated a current one.
#
# These are small, mutable, and crawler-facing -> no-cache + an explicit
# CloudFront invalidation (they sit at root paths, not /data/*, so the
# callers' '/data/*' invalidation never covered them).
PUBLIC_DIR="frontend/public"
if [ -d "$PUBLIC_DIR" ]; then
  aws s3 sync "$PUBLIC_DIR/" "$S3_BUCKET/" \
    --cache-control "no-cache" \
    --exclude "*" \
    --include "sitemap.xml" --include "sitemap_index.xml" --include "news-sitemap.xml" \
    --include "robots.txt" --include "llms.txt" --include "llms-full.txt" \
    --include "skill.md" --include ".well-known/*" \
    --no-follow-symlinks
  if [ -n "$CF" ]; then
    aws cloudfront create-invalidation --distribution-id "$CF" \
      --paths '/sitemap.xml' '/sitemap_index.xml' '/news-sitemap.xml' \
              '/robots.txt' '/llms.txt' '/llms-full.txt' '/skill.md' \
              '/.well-known/*' \
      --query 'Invalidation.Id' --output text
  fi
fi

fi  # PRERENDER_ONLY

# ---- Pre-rendered per-clip HTML pages (prerender.py). Uploaded to the bucket
# ROOT as extension-less keys `meeting/<id>` (+ the flat static routes) so the
# exact path CloudFront requests resolves to a real 200 document instead of
# the S3-404 -> index.html shell that made every meeting page claim the
# homepage canonical. `--content-type` is REQUIRED: with no extension the CLI
# would guess binary/octet-stream and browsers would download the page.
# Generation only rewrites files whose content changed (mtime preserved
# otherwise), so this sync pushes just the deltas; invalidate `/meeting/*`
# (one wildcard path) only when something actually uploaded.
PRERENDER_DIR="$SRC/prerender"
if [ -d "$PRERENDER_DIR" ]; then
  sync_out="$(aws s3 sync "$PRERENDER_DIR/" "$S3_BUCKET/" \
    --exclude "*" \
    --include "meeting/*" --include "ask" --include "chat" --include "about" --include "corrections" \
    --exclude "*.tmp" \
    --content-type "text/html; charset=utf-8" \
    --cache-control "public, max-age=300, s-maxage=86400, stale-while-revalidate=86400" \
    --no-follow-symlinks 2>&1 | tee /dev/stderr)"
  paths=()
  if echo "$sync_out" | grep -q "upload: .*/meeting/"; then paths+=('/meeting/*'); fi
  for pg in ask chat about corrections; do
    if echo "$sync_out" | grep -q "upload: .*prerender/$pg to "; then paths+=("/$pg"); fi
  done
  if [ -n "$CF" ] && [ "${#paths[@]}" -gt 0 ]; then
    echo "Invalidating pre-rendered pages: ${paths[*]}"
    aws cloudfront create-invalidation --distribution-id "$CF" \
      --paths "${paths[@]}" --query 'Invalidation.Id' --output text
  fi
fi
