#!/usr/bin/env bash
#
# Lean incremental ingest for the LFUCG meeting archive.
#
# Runs every 6h on weekdays (see crontab.txt). Co-located design: the
# pipeline and the RAG/MCP API share /opt/fuzzy-potato/lfucg_output on
# local disk, so surfacing new data is a sub-second `systemctl restart`
# of the API (it reloads chroma_db + search.db + clip_metadata lazily on
# the next request) — NO Docker build, NO ECR push, NO App Runner redeploy.
#
# Scope is intentionally LEAN: probe + process new clips + RAG ingest.
# The expensive archive-wide sweeps (backfill-docs, upgrade-summaries,
# tables-of-motions) live in backfill_weekly.sh.
#
set -euo pipefail
export AWS_PAGER=""
# cron runs with a minimal PATH; make uv + aws reachable.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

REPO="${FUZZY_POTATO_DIR:-/opt/fuzzy-potato}"
cd "$REPO"

# Load OPENAI/ANTHROPIC/FEEDS keys + S3/CF config from .env
set -a
# shellcheck disable=SC1091
[ -f .env ] && source .env
set +a

S3_BUCKET="${S3_BUCKET:-s3://public-meetings}"
CLOUDFRONT_DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-E8OIXOXDRETLZ}"
RAG_SERVICE="${RAG_SERVICE:-lfucg-rag}"
STATE="lfucg_output/state.json"

read_last() {
  python3 -c "import json; print(json.load(open('$STATE')).get('last_processed_clip_id',''))" 2>/dev/null || echo ""
}

log() { echo "==> [$(date -Is)] $*"; }

before="$(read_last)"

log "Probing Granicus for new clip IDs"
uv run python probe_clips.py

log "Processing new clips (--auto --rag --no-audio)"
# --auto: process from last_processed_clip_id + 1
# --rag:  auto-ingest each processed clip into ChromaDB
# end-of-batch: rebuilds index.json + SEO artifacts (clip.md/llms.txt) + search.db
uv run python main.py --auto --rag --no-audio

log "Backstop: ingest any new clips that slipped (idempotent)"
uv run python -m rag.ingest --new

after="$(read_last)"

# Skip the downstream churn (restart / S3 / CloudFront / feeds) when nothing
# new landed. last_processed_clip_id is the cheap proxy; the weekly backfill
# job handles edits to OLDER clips and runs its own sync.
if [ "$before" = "$after" ]; then
  log "No new clips (last_processed_clip_id=$before unchanged) — skipping restart/S3/CF/feeds"
  exit 0
fi

log "New data ($before -> $after) — refreshing RAG API caches"
# Prefer the graceful /admin/reload hook (drops cached chroma/search.db/
# metadata without dropping in-flight /api/ask). Fall back to a full
# service restart if the hook isn't configured or fails.
if [ -n "${RELOAD_TOKEN:-}" ] && curl -fsS -m 10 -X POST \
     -H "X-Reload-Token: $RELOAD_TOKEN" http://127.0.0.1:8000/admin/reload >/dev/null; then
  log "RAG API caches reloaded gracefully (no restart)"
else
  log "Reload hook unavailable — restarting $RAG_SERVICE"
  sudo systemctl restart "$RAG_SERVICE"
fi

log "Syncing per-clip data + index to S3"
bash deploy/lightsail/sync_data_s3.sh

log "Invalidating CloudFront /data/*"
aws cloudfront create-invalidation \
  --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
  --paths '/data/*' \
  --query 'Invalidation.Id' --output text

if [ -n "${FEEDS_API_TOKEN:-}" ] && [ -n "${FEEDS_WEBHOOK_URL:-}" ]; then
  log "Pinging feeds to pull new archive items"
  curl -sS -X POST \
    -H "Authorization: Bearer $FEEDS_API_TOKEN" \
    -H "Content-Type: application/json" \
    "$FEEDS_WEBHOOK_URL" || true
  echo ""
fi

log "Done"
