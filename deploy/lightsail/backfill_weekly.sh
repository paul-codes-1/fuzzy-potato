#!/usr/bin/env bash
#
# Heavy archive-wide sweeps — pulled OUT of the 6-hourly lean ingest so
# they don't redundantly re-scan all ~4,700 clips 4x/day. Run weekly
# (see crontab.txt). All steps are idempotent / resumable:
#   - backfill-docs: fetches missing minutes/agenda (+ regenerate summary)
#   - backfill-tables-of-motions: applies official motions from agenda packets
# (v2 summary upgrades run DAILY now — see summaries_cron.sh — so they're
#  no longer part of this weekly sweep.)
#
set -euo pipefail
export AWS_PAGER=""
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

REPO="${FUZZY_POTATO_DIR:-/opt/fuzzy-potato}"
cd "$REPO"
set -a
# shellcheck disable=SC1091
[ -f .env ] && source .env
set +a

CLOUDFRONT_DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-}"
RAG_SERVICE="${RAG_SERVICE:-lfucg-rag}"

log() { echo "==> [$(date -Is)] $*"; }

# --backfill-docs alone is idempotent: it only fetches docs for clips that
# are MISSING them, so once converged it's a cheap no-op. --regenerate-summary
# is the NON-idempotent flag (re-bills ~$0.15/clip it touches) — keep it OFF
# by default and only flip REGEN_SUMMARIES=1 deliberately, e.g. after a
# summary parser/prompt change.
log "Backfilling missing minutes/agenda"
BACKFILL_ARGS=(--backfill-docs)
if [ "${REGEN_SUMMARIES:-0}" = "1" ]; then
  log "REGEN_SUMMARIES=1 — also regenerating summaries for touched clips"
  BACKFILL_ARGS+=(--regenerate-summary)
fi
uv run python main.py "${BACKFILL_ARGS[@]}"

log "Applying official Tables of Motions from agenda packets"
uv run python main.py --backfill-tables-of-motions

log "Refreshing RAG API + syncing S3 + invalidating CloudFront"
if [ -n "${RELOAD_TOKEN:-}" ] && curl -fsS -m 10 -X POST \
     -H "X-Reload-Token: $RELOAD_TOKEN" http://127.0.0.1:8000/admin/reload >/dev/null; then
  log "RAG API caches reloaded gracefully (no restart)"
else
  sudo systemctl restart "$RAG_SERVICE"
fi
bash deploy/lightsail/sync_data_s3.sh
if [ -n "$CLOUDFRONT_DISTRIBUTION_ID" ]; then
  aws cloudfront create-invalidation \
    --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths '/data/*' \
    --query 'Invalidation.Id' --output text
fi

log "Weekly backfill done"
