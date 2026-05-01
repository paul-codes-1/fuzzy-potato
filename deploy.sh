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

# Data JSON — must revalidate every load (otherwise stale meetings list)
echo "==> Syncing data (no-cache)..."
aws s3 sync frontend/dist/data/ "$S3_BUCKET/data/" --size-only \
  --cache-control "no-cache" \
  --exclude "*.mp3" \
  --exclude "*.mp4" \
  --exclude "*.part" \
  --exclude "*.ytdl" \
  --exclude "chroma_db/*"

# Ensure existing top-level data JSON files have no-cache header
# (sync --size-only skips unchanged files, leaving their old metadata)
echo "==> Ensuring no-cache header on top-level data JSON..."
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
