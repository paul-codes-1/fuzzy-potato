FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY rag/ rag/
COPY main.py .
# Top-level modules pulled out of main.py during the cleanup PRs (#6, #7).
# `clients.py` is imported by rag/server.py + rag/query.py + rag/ingest.py
# so it MUST be in the image; the others are main.py-only but small and
# kept here so the image faithfully mirrors the repo root.
COPY clients.py documents.py seo.py summary_v2.py config.py ./
# Per-jurisdiction config (config.py falls back to built-in LFUCG defaults
# if absent, but ship it so onboarding another tenant is just a new TOML).
COPY jurisdictions/ jurisdictions/

# Helper used by main.py + tests, also imported when /api/search runs
# the standalone search-db builder. Lightweight (no heavy deps).
COPY scripts/__init__.py scripts/build_search_db.py scripts/

# Install project with rag dependencies
RUN pip install --no-cache-dir ".[rag]"

# Copy baked-in data (chroma_db + clip metadata + FTS5 search.db). The
# search.db drives /api/search; it's rebuilt on every ingest_all by
# pipeline.generate_search_index() so the in-image copy stays fresh.
COPY lfucg_output/chroma_db/ lfucg_output/chroma_db/
COPY lfucg_output/clips/ lfucg_output/clips/
COPY lfucg_output/search.db lfucg_output/search.db

# Copy entrypoint
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV LFUCG_OUTPUT_DIR=/app/lfucg_output

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
