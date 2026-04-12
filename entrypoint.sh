#!/bin/bash
set -e

echo "==> Starting API server on port 8000..."
exec uvicorn api.server:app --host 0.0.0.0 --port 8000
