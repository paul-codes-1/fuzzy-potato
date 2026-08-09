#!/usr/bin/env bash
#
# Weekly off-box backup of the RAG indexes to S3. Tars vec.db (the live
# sqlite-vec store) + search.db + rag_state.json (+ chroma_db + state.json where
# they still exist) and uploads to the lt-backups bucket keyed by
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

# Always clean up the tmp tar AND the vec.db snapshot dir, even on an aws
# failure mid-upload.
VEC_SNAP_DIR=""
trap 'rm -f "$TARBALL"; [ -n "$VEC_SNAP_DIR" ] && rm -rf "$VEC_SNAP_DIR"' EXIT

log "Tarring indexes from $OUTPUT_DIR"
# Only include paths that exist: chroma_db is gone on boxes migrated to the
# sqlite-vec store; state.json is absent on doc-driven boxes. Each entry is
# [ -e ]-guarded so a box with any subset still backs up cleanly.
paths=()
for p in chroma_db search.db rag_state.json state.json; do
  [ -e "$OUTPUT_DIR/$p" ] && paths+=("$p")
done

# vec.db is the LIVE store (held open + written by the RAG API), so tarring the
# on-disk file directly can capture a torn page mid-write. Snapshot it
# CONSISTENTLY with `sqlite3 .backup` into a temp dir first, then tar the
# snapshot under the name vec.db. Guarded so boxes still on chroma (no vec.db)
# and boxes lacking sqlite3 degrade gracefully rather than shipping a bad file.
tar_vec_args=()
if [ -e "$OUTPUT_DIR/vec.db" ]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    VEC_SNAP_DIR="$(mktemp -d)"
    if sqlite3 "$OUTPUT_DIR/vec.db" ".backup '$VEC_SNAP_DIR/vec.db'"; then
      tar_vec_args=(-C "$VEC_SNAP_DIR" vec.db)
    else
      log "WARNING: sqlite3 .backup of vec.db failed — omitting vec.db from this backup"
      rm -rf "$VEC_SNAP_DIR"; VEC_SNAP_DIR=""
    fi
  else
    log "WARNING: sqlite3 not found — cannot safely snapshot vec.db; omitting it"
  fi
fi

if [ ${#paths[@]} -eq 0 ] && [ ${#tar_vec_args[@]} -eq 0 ]; then
  log "Nothing to back up under $OUTPUT_DIR — skipping"
  exit 0
fi
# GNU tar applies each -C positionally, so the vec.db snapshot is pulled from
# its own dir while the rest come from $OUTPUT_DIR.
tar -czf "$TARBALL" -C "$OUTPUT_DIR" "${paths[@]}" "${tar_vec_args[@]}"
log "Wrote $TARBALL ($(du -h "$TARBALL" | cut -f1))"

log "Uploading to $BUCKET/indexes-${STAMP}.tar.gz"
aws s3 cp "$TARBALL" "$BUCKET/indexes-${STAMP}.tar.gz"

# Success-only heartbeat (runs before the EXIT trap rm's the tarball).
/opt/fuzzy-potato/deploy/lightsail/heartbeat.sh "${SLUG}-index-backup"
log "Index backup done"
