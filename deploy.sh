#!/bin/bash
set -e

# Disable AWS CLI pager so commands don't block on `less` waiting for `q`.
export AWS_PAGER=""

S3_BUCKET="s3://public-meetings"
# Set your CloudFront distribution ID here or as an env var
CLOUDFRONT_DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-}"

echo "==> Building frontend..."
cd frontend
npm run build
cd ..

# Hashed JS/CSS bundles — content-hashed filenames, safe to cache forever
echo "==> Syncing hashed assets (immutable, 1y cache)..."
aws s3 sync frontend/dist/assets/ "$S3_BUCKET/assets/" \
  --cache-control "public, max-age=31536000, immutable" \
  --delete

# Root files (index.html, favicons) — must revalidate every load
echo "==> Syncing root files (no-cache)..."
aws s3 sync frontend/dist/ "$S3_BUCKET" \
  --exclude "data/*" --exclude "assets/*" --delete \
  --cache-control "no-cache"

# Cache strategy for /data/* — split into pools by mutability so CloudFront
# can serve the per-clip tree from edge instead of revalidating against S3
# on every request. Every deploy already runs an `--paths "/*"`
# invalidation below, so even with year-long TTLs the next deploy still
# flushes everything. Existing files (uploaded before this layout) keep
# their old headers because `--size-only` skips them — run
# `scripts/restamp-cache-headers.sh` once after merge to retro-fix the
# tree (~13k objects, 1–2 min).

# Per-clip PDFs (agendas + minutes): never change after processing → 1y
# immutable. Run first because the sync below excludes them.
echo "==> Syncing per-clip PDFs (immutable, 1y cache)..."
aws s3 sync frontend/dist/data/ "$S3_BUCKET/data/" --size-only \
  --exclude "*" \
  --include "clips/*/*.pdf" \
  --cache-control "public, max-age=31536000, immutable"

# Per-clip Markdown alternate: served at /data/clips/<id>/clip.md for AI
# agents and discoverable via <link rel="alternate" type="text/markdown">.
# Functionally immutable per processing run → 1d fresh + 7d
# stale-while-revalidate. Content-Type override so it isn't served as
# octet-stream.
echo "==> Syncing per-clip Markdown alternates..."
aws s3 sync frontend/dist/data/ "$S3_BUCKET/data/" --size-only \
  --exclude "*" \
  --include "clips/*/clip.md" \
  --content-type "text/markdown; charset=utf-8" \
  --cache-control "public, max-age=86400, stale-while-revalidate=604800"

# Per-clip text + JSON data (transcripts, summaries, agenda txt, minutes
# txt, metadata.json, extracted_facts.json): functionally immutable per
# processing run → 1d fresh + 7d SWR. The cron CloudFront invalidation
# still flushes them on the rare reprocess.
echo "==> Syncing per-clip text + JSON data (1d fresh, 7d SWR)..."
aws s3 sync frontend/dist/data/ "$S3_BUCKET/data/" --size-only \
  --exclude "*" \
  --include "clips/*/*.txt" \
  --include "clips/*/*.json" \
  --include "clips/*/*.html" \
  --cache-control "public, max-age=86400, stale-while-revalidate=604800"

# Top-level data — index.json mutates every cron, search_index chunks
# rotate, rag_state.json updates per ingest. Must revalidate.
echo "==> Syncing top-level data (no-cache)..."
aws s3 sync frontend/dist/data/ "$S3_BUCKET/data/" --size-only \
  --cache-control "no-cache" \
  --exclude "*.mp3" \
  --exclude "*.mp4" \
  --exclude "*.part" \
  --exclude "*.ytdl" \
  --exclude "chroma_db/*" \
  --exclude "clips/*"

# Re-stamp top-level JSON: --size-only skips unchanged files, which
# leaves their old metadata in place; re-cp with REPLACE forces the
# no-cache header to take effect even when the file content is stable.
echo "==> Re-stamping top-level data JSON..."
aws s3 ls "$S3_BUCKET/data/" | awk '/\.json$/ {print $4}' | while read -r f; do
  aws s3 cp "$S3_BUCKET/data/$f" "$S3_BUCKET/data/$f" \
    --metadata-directive REPLACE \
    --cache-control "no-cache" \
    --content-type application/json \
    > /dev/null
done

if [ -n "$CLOUDFRONT_DISTRIBUTION_ID" ]; then
  echo "==> Invalidating CloudFront cache..."
  aws cloudfront create-invalidation \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths "/*" \
    --query 'Invalidation.Id' \
    --output text
  echo "==> Invalidation created. Usually completes in 1-2 minutes."
else
  echo "==> Skipping CloudFront invalidation (CLOUDFRONT_DISTRIBUTION_ID not set)"
  echo "   Set it with: export CLOUDFRONT_DISTRIBUTION_ID=E8OIXOXDRETLZ"
fi

echo "==> Deploy complete!"
