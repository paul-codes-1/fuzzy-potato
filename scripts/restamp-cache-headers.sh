#!/bin/bash
# Retroactively re-stamp Cache-Control headers on already-uploaded
# /data/clips/* objects. Needed once after the cache-headers PR merges
# because `aws s3 sync --size-only` skips unchanged files and so leaves
# their old `no-cache` metadata in place. Subsequent deploys upload new
# files with the correct headers via deploy.sh.
#
# Idempotent — re-running is safe but does nothing useful. Uses S3
# server-side CopyObject (--metadata-directive REPLACE) so the object
# data isn't re-uploaded; only the metadata is rewritten.
#
# Runtime: ~13k objects. AWS CLI parallelizes internally. Expect 1–3 min.

set -euo pipefail

export AWS_PAGER=""

S3_BUCKET="${S3_BUCKET:-s3://public-meetings}"
DATA_PREFIX="${S3_BUCKET}/data/clips/"

PDF_CACHE="public, max-age=31536000, immutable"
DATA_CACHE="public, max-age=86400, stale-while-revalidate=604800"

echo "==> Restamping per-clip PDFs (1y immutable) under ${DATA_PREFIX}"
aws s3 cp "$DATA_PREFIX" "$DATA_PREFIX" \
  --recursive \
  --exclude "*" \
  --include "*.pdf" \
  --metadata-directive REPLACE \
  --cache-control "$PDF_CACHE" \
  --no-progress

echo "==> Restamping clip.md alternates (1d fresh, 7d SWR, text/markdown)"
aws s3 cp "$DATA_PREFIX" "$DATA_PREFIX" \
  --recursive \
  --exclude "*" \
  --include "*/clip.md" \
  --metadata-directive REPLACE \
  --cache-control "$DATA_CACHE" \
  --content-type "text/markdown; charset=utf-8" \
  --no-progress

echo "==> Restamping per-clip text files (1d fresh, 7d SWR)"
aws s3 cp "$DATA_PREFIX" "$DATA_PREFIX" \
  --recursive \
  --exclude "*" \
  --include "*.txt" \
  --metadata-directive REPLACE \
  --cache-control "$DATA_CACHE" \
  --content-type "text/plain; charset=utf-8" \
  --no-progress

echo "==> Restamping per-clip JSON files (1d fresh, 7d SWR)"
aws s3 cp "$DATA_PREFIX" "$DATA_PREFIX" \
  --recursive \
  --exclude "*" \
  --include "*.json" \
  --metadata-directive REPLACE \
  --cache-control "$DATA_CACHE" \
  --content-type "application/json" \
  --no-progress

echo "==> Restamping per-clip HTML files (1d fresh, 7d SWR)"
aws s3 cp "$DATA_PREFIX" "$DATA_PREFIX" \
  --recursive \
  --exclude "*" \
  --include "*.html" \
  --metadata-directive REPLACE \
  --cache-control "$DATA_CACHE" \
  --content-type "text/html; charset=utf-8" \
  --no-progress

echo "==> Done. Spot-check with:"
echo "    aws s3api head-object --bucket ${S3_BUCKET#s3://} --key data/clips/6757/summary.txt | jq '{CacheControl, ContentType}'"
