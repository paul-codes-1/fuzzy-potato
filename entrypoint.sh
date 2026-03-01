#!/bin/bash
set -e

echo "==> Starting RAG API server on port 8000..."
exec uvicorn rag.server:app --host 0.0.0.0 --port 8000
