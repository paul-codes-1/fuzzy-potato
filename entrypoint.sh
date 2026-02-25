#!/bin/bash
set -e

OUTPUT_DIR="${LFUCG_OUTPUT_DIR:-/app/lfucg_output}"

# Sync data from S3 if bucket is configured
if [ -n "$S3_BUCKET" ]; then
  PREFIX="${S3_DATA_PREFIX:-data/}"
  S3_BASE="s3://${S3_BUCKET}/${PREFIX}"

  echo "==> Syncing chroma_db from ${S3_BASE}chroma_db/ ..."
  mkdir -p "${OUTPUT_DIR}/chroma_db"
  aws s3 sync "${S3_BASE}chroma_db/" "${OUTPUT_DIR}/chroma_db/" --quiet

  echo "==> Syncing clip metadata.json files from ${S3_BASE}clips/ ..."
  mkdir -p "${OUTPUT_DIR}/clips"
  aws s3 sync "${S3_BASE}clips/" "${OUTPUT_DIR}/clips/" \
    --exclude "*" --include "*/metadata.json" --quiet

  echo "==> S3 sync complete."
else
  echo "==> No S3_BUCKET set, using local data at ${OUTPUT_DIR}"
fi

echo "==> Starting RAG API server on port 8000..."
exec uvicorn rag.server:app --host 0.0.0.0 --port 8000
