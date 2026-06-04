#!/usr/bin/env bash
#
# Lean S3 sync of the per-clip archive + top-level index, sourced
# DIRECTLY from lfucg_output/ — no `npm run build`, no 11GB copy into
# frontend/dist/data/ (that copy only matters when the SPA *bundle*
# changes, which is a separate frontend code deploy via ../../deploy.sh).
#
# Mirrors the cache-control pools in ../../deploy.sh so CloudFront keeps
# serving the per-clip tree from edge. `--size-only` means only new/
# changed clip files upload, so each cron run pushes just the deltas.
#
set -euo pipefail
export AWS_PAGER=""

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
S3_BUCKET="${S3_BUCKET:-s3://public-meetings}"
SRC="lfucg_output"

# Per-clip PDFs (agendas + minutes): never change after processing -> 1y immutable.
aws s3 sync "$SRC/" "$S3_BUCKET/data/" --size-only \
  --exclude "*" --include "clips/*/*.pdf" \
  --cache-control "public, max-age=31536000, immutable"

# Per-clip Markdown alternates (clip.md): AI-agent discovery surface,
# functionally immutable per processing run -> 1d fresh + 7d SWR.
aws s3 sync "$SRC/" "$S3_BUCKET/data/" --size-only \
  --exclude "*" --include "clips/*/clip.md" \
  --content-type "text/markdown; charset=utf-8" \
  --cache-control "public, max-age=86400, stale-while-revalidate=604800"

# Per-clip text + JSON + HTML (transcripts, summaries, agenda/minutes txt,
# metadata.json, extracted_facts.json): 1d fresh + 7d SWR.
aws s3 sync "$SRC/" "$S3_BUCKET/data/" --size-only \
  --exclude "*" \
  --include "clips/*/*.txt" \
  --include "clips/*/*.json" \
  --include "clips/*/*.html" \
  --cache-control "public, max-age=86400, stale-while-revalidate=604800"

# Top-level data (index.json, llms.txt, available_clips.json, rag_state.json,
# state.json): mutates every run -> no-cache. Exclude the big local-only
# artifacts: chroma_db/ and search.db are served by the LOCAL RAG API now,
# NOT from S3, so there's no reason to ship them (search.db = 300MB,
# chroma_db = 5.1GB). Also exclude raw media + per-clip files (handled above).
aws s3 sync "$SRC/" "$S3_BUCKET/data/" --size-only \
  --cache-control "no-cache" \
  --exclude "clips/*" \
  --exclude "chroma_db/*" \
  --exclude "search.db" \
  --exclude "*.mp3" --exclude "*.mp4" --exclude "*.part" --exclude "*.ytdl"
