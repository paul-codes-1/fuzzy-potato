#!/usr/bin/env bash
#
# Force-regenerate v2 summaries (GPT-4o facts + Claude narrative) for an
# EXPLICIT clip-ID range, then make the results visible everywhere. Use
# after an extraction-prompt fix (e.g. the 2026-06 timestamped-transcript
# change) when already-summarized clips need their facts rebuilt.
#
#   ./deploy/lightsail/regen_summaries_range.sh 6301 6798
#
# Unlike summaries_cron.sh (which only fills MISSING facts), the explicit
# range passed to --upgrade-summaries bypasses the already-done skip, so
# every clip in the range with a transcript is re-extracted (~$0.15/clip).
#
# Sequence notes:
#   - Re-extraction OVERWRITES official Table-of-Motions data with
#     transcript-derived motions, so --backfill-tables-of-motions runs
#     right after to re-apply the official record (idempotent: it only
#     touches clips whose facts lost the motions_source marker).
#   - Re-ingest is per-clip and idempotent (delete-then-add in ChromaDB).
#   - Run under the shared pipeline lock so the 6-hourly ingest and the
#     nightly summaries cron skip cleanly while this is in flight:
#       flock /tmp/lfucg-pipeline.lock ./deploy/lightsail/regen_summaries_range.sh 6301 6798
#
set -euo pipefail
export AWS_PAGER=""
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"

START="${1:?usage: regen_summaries_range.sh START END}"
END="${2:?usage: regen_summaries_range.sh START END}"

REPO="${FUZZY_POTATO_DIR:-/opt/fuzzy-potato}"
cd "$REPO"
set -a
# shellcheck disable=SC1091
[ -f .env ] && source .env
set +a

CLOUDFRONT_DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-}"
RAG_SERVICE="${RAG_SERVICE:-lfucg-rag}"
log() { echo "==> [$(date -Is)] $*"; }

log "Force-regenerating v2 summaries for clips $START-$END"
uv run python main.py --upgrade-summaries "$START" "$END"

log "Re-applying official Tables of Motions (regen overwrote them)"
uv run python main.py --backfill-tables-of-motions --no-reingest

log "Re-ingesting clips $START-$END into ChromaDB (idempotent)"
for id in $(seq "$START" "$END"); do
  [ -f "lfucg_output/clips/$id/metadata.json" ] || continue
  uv run python -m rag.ingest --clip "$id"
done

log "Regenerating index + SEO artifacts + search.db (clip.md picks up new summaries)"
uv run python main.py --generate-index

log "Refreshing RAG API caches"
if [ -n "${RELOAD_TOKEN:-}" ] && curl -fsS -m 10 -X POST \
     -H "X-Reload-Token: $RELOAD_TOKEN" http://127.0.0.1:8000/admin/reload >/dev/null; then
  log "RAG API caches reloaded gracefully"
else
  log "Reload hook unavailable — restarting $RAG_SERVICE"
  sudo systemctl restart "$RAG_SERVICE"
fi

log "Syncing data to S3"
bash deploy/lightsail/sync_data_s3.sh

if [ -n "$CLOUDFRONT_DISTRIBUTION_ID" ]; then
  log "Invalidating CloudFront /data/*"
  aws cloudfront create-invalidation --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" \
    --paths '/data/*' --query 'Invalidation.Id' --output text
fi

log "Range regen done"
