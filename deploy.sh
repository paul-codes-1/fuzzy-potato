#!/bin/bash
set -e

S3_BUCKET="s3://public-meetings"
# Set your CloudFront distribution ID here or as an env var
CLOUDFRONT_DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-}"

echo "==> Building frontend..."
cd frontend
npm run build
cd ..

echo "==> Syncing frontend to S3 (excluding data/)..."
aws s3 sync frontend/dist/ "$S3_BUCKET" --exclude "data/*" --delete

echo "==> Syncing data to S3..."
aws s3 sync frontend/dist/data/ "$S3_BUCKET/data/" --size-only

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
  echo "   Set it with: export CLOUDFRONT_DISTRIBUTION_ID=E1234567890"
fi

echo "==> Deploy complete!"
