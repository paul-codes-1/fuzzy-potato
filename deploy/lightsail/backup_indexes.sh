#!/usr/bin/env bash
#
# Weekly off-box backup of the RAG indexes to S3. Tars chroma_db + search.db +
# rag_state.json (+ state.json) and uploads to the lt-backups bucket keyed by
# jurisdiction + date, then fires a dead-man heartbeat. Best-effort: a missing
# bucket / IAM permission fails LOUD into the cron log (acceptable until IAM is
# provisioned) and — because it fails before the heartbeat — sends no ping.
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

OUTPUT_DIR="${LFUCG_OUTPUT_DIR:-lfucg_output}"
SLUG="${JURISDICTION:-lfucg}"
BUCKET="s3://lt-backups-861476138515/fuzzy-potato/${SLUG}"
STAMP="$(date +%Y%m%d)"
TARBALL="/tmp/${SLUG}-indexes-${STAMP}.tar.gz"

log() { echo "==> [$(date -Is)] $*"; }
# Always clean up the tmp tar, even on an aws failure mid-upload.
trap 'rm -f "$TARBALL"' EXIT

log "Tarring indexes from $OUTPUT_DIR"
# Only include paths that exist (state.json is absent on doc-driven boxes).
paths=()
for p in chroma_db search.db rag_state.json state.json; do
  [ -e "$OUTPUT_DIR/$p" ] && paths+=("$p")
done
if [ ${#paths[@]} -eq 0 ]; then
  log "Nothing to back up under $OUTPUT_DIR — skipping"
  exit 0
fi
tar -czf "$TARBALL" -C "$OUTPUT_DIR" "${paths[@]}"
log "Wrote $TARBALL ($(du -h "$TARBALL" | cut -f1))"

log "Uploading to $BUCKET/indexes-${STAMP}.tar.gz"
aws s3 cp "$TARBALL" "$BUCKET/indexes-${STAMP}.tar.gz"

# Success-only heartbeat (runs before the EXIT trap rm's the tarball).
/opt/fuzzy-potato/deploy/lightsail/heartbeat.sh "${SLUG}-index-backup"
log "Index backup done"
