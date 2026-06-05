#!/usr/bin/env bash
#
# Daily v2-summary upgrade. The lean 6h ingest (ingest_cron.sh) makes new clips
# searchable fast off the Granicus VTT captions but DEFERS the expensive two-pass
# summary (GPT-4o facts + Claude narrative). This job generates those summaries
# for any clip that has a transcript but no extracted_facts.json yet, then makes
# them visible:
#
#   --upgrade-summaries only WRITES summary.txt + extracted_facts.json — it does
#   NOT re-ingest into ChromaDB or rebuild search.db. So this script re-ingests
#   the clips it just upgraded (rag.ingest --clip is idempotent: it deletes a
#   clip's existing chunks before re-adding), rebuilds search.db, and refreshes
#   the API + S3 + CDN.
#
# Cheap + resumable: clips that already have facts are skipped, and clips with no
# transcript are skipped with no LLM cost — so day-to-day this only touches the
# handful of clips the previous day's ingest left as VTT placeholders.
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

# Which clips have facts BEFORE the run (basenames of dirs that have the file).
facts_set() { find lfucg_output/clips -name extracted_facts.json -printf '%h\n' 2>/dev/null | xargs -r -n1 basename | sort; }
before="$(facts_set)"

log "Upgrading v2 summaries for clips with a transcript but no facts"
uv run python main.py --upgrade-summaries --max 9999

after="$(facts_set)"
# Clips that gained facts this run = the ones we just upgraded.
upgraded="$(comm -13 <(echo "$before") <(echo "$after") | grep -E '^[0-9]+$' || true)"

if [ -z "$upgraded" ]; then
  log "No clips upgraded — nothing to re-ingest. Done."
  exit 0
fi
log "Upgraded clips: $(echo "$upgraded" | tr '\n' ' ')"

log "Re-ingesting upgraded clips into ChromaDB (idempotent)"
for id in $upgraded; do
  uv run python -m rag.ingest --clip "$id"
done

log "Rebuilding search.db"
uv run python main.py --build-search-db

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

log "Daily summaries done"
