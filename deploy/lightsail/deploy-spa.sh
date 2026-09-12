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
#   LFUCG:  JURISDICTION=lfucg S3_BUCKET=s3://public-meetings \
#           CLOUDFRONT_DISTRIBUTION_ID=E8OIXOXDRETLZ PRERENDER_SSH_HOST=lfucg-meetings \
#           ./deploy/lightsail/deploy-spa.sh
#
# Pre-rendered per-clip pages (prerender.py) are copies of this shell with the
# head rewritten, so they pin the asset hashes that were live when the BOX
# generated them. Two consequences:
#   1. NEVER `--delete` old hashes from /assets/ — a page generated before
#      this deploy must keep loading until it is regenerated. Stale bundles
#      cost cents of S3; a page pointing at a deleted bundle is a blank site.
#   2. After the new index.html is live, regenerate + re-upload every page so
#      they pick up the new hashes. With PRERENDER_SSH_HOST set that happens
#      here over ssh; otherwise the box's next 6h ingest cron does it (the
#      template hash changing forces a full re-render) and the old pages keep
#      working meanwhile thanks to (1).
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

log "Uploading hashed assets (immutable; old hashes are KEPT for pre-rendered pages)"
aws s3 sync frontend/dist/assets/ "$S3_BUCKET/assets/" \
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

PRERENDER_HOST="${PRERENDER_SSH_HOST:-}"
if [ -n "$PRERENDER_HOST" ]; then
  log "Regenerating pre-rendered per-clip pages on $PRERENDER_HOST against the new shell"
  # shellcheck disable=SC2029
  ssh "$PRERENDER_HOST" 'cd /opt/fuzzy-potato && set -a && . ./.env && set +a && \
    "$HOME/.local/bin/uv" run python main.py --prerender --full && \
    bash deploy/lightsail/sync_data_s3.sh --prerender-only'
else
  log "PRERENDER_SSH_HOST unset — pre-rendered pages will be refreshed by the box's next ingest cron"
fi

log "SPA deploy done. (Data + SEO artifacts are deployed separately by the box.)"
