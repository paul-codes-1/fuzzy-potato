#!/usr/bin/env bash
#
# Per-jurisdiction SPA deploy (run from a workstation that can `npm run build`).
#
# The SPA is ONE bundle served to every county; only the per-county data +
# SEO artifacts differ, and those are generated + uploaded BY THE BOX (the
# pipeline's sync_data_s3.sh + the box's frontend/public/*). So this script
# uploads ONLY the bundle: dist/assets/ + a jurisdiction-rendered index.html.
# It deliberately does NOT touch /data/* or the root SEO files (llms.txt,
# robots.txt, sitemaps, skill.md, .well-known/) — uploading the LFUCG copies
# baked into frontend/public/ would clobber the county's correct ones.
#
# Usage:
#   JURISDICTION=paris \
#   S3_BUCKET=s3://paris-civic-meetings \
#   CLOUDFRONT_DISTRIBUTION_ID=E28B7ORY9035LU \
#   ./deploy/lightsail/deploy-spa.sh
#
set -euo pipefail
export AWS_PAGER=""
: "${JURISDICTION:?set JURISDICTION (e.g. paris)}"
: "${S3_BUCKET:?set S3_BUCKET (e.g. s3://paris-civic-meetings)}"
CF="${CLOUDFRONT_DISTRIBUTION_ID:-}"

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
log() { echo "==> $*"; }

log "Building frontend bundle"
(cd frontend && npm run build)

log "Rendering index.html static meta for JURISDICTION=$JURISDICTION"
uv run python scripts/render_index_html.py

log "Uploading hashed assets (immutable, --delete drops the old bundle)"
aws s3 sync frontend/dist/assets/ "$S3_BUCKET/assets/" --delete \
  --cache-control "public, max-age=31536000, immutable"

log "Uploading index.html (no-cache)"
aws s3 cp frontend/dist/index.html "$S3_BUCKET/index.html" \
  --cache-control "no-cache" --content-type "text/html"

if [ -n "$CF" ]; then
  log "Invalidating CloudFront ($CF): /index.html + /assets/*"
  aws cloudfront create-invalidation --distribution-id "$CF" \
    --paths '/index.html' '/assets/*' --query 'Invalidation.Id' --output text
else
  log "CLOUDFRONT_DISTRIBUTION_ID unset — skipping invalidation"
fi

log "SPA deploy done. (Data + SEO artifacts are deployed separately by the box.)"
