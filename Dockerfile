# syntax=docker/dockerfile:1

# =============================================================================
# Stage 1: Builder - install dependencies and compile wheels
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build-time system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxmlsec1-dev \
        libxmlsec1-openssl \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install pip dependencies into a virtual environment for clean copy
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only dependency metadata first (layer caching)
COPY pyproject.toml .

# Install the API extra (includes core + search/LLM deps) without the project itself
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir ".[api]" || true

# Copy source code
COPY api/ api/
COPY main.py .
COPY summary_v2.py* ./
COPY probe_clips.py* ./
COPY download_docs.py* ./

# Install the project itself (now that source is present)
RUN pip install --no-cache-dir ".[api]"

# =============================================================================
# Stage 2: Runtime - minimal image with only what's needed
# =============================================================================
FROM python:3.11-slim AS runtime

# OCI standard labels
LABEL org.opencontainers.image.title="CivicLens" \
      org.opencontainers.image.description="AI-powered government meeting archive platform" \
      org.opencontainers.image.vendor="CivicLens" \
      org.opencontainers.image.source="https://github.com/paul-codes-1/elk-grove-meetings" \
      org.opencontainers.image.licenses="MIT"

# Install runtime system dependencies + tini for proper PID 1 signal handling
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        tesseract-ocr \
        poppler-utils \
        libxml2 \
        libxmlsec1 \
        libxmlsec1-openssl \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/* /var/tmp/*

# Create non-root user
RUN groupadd --gid 1000 civiclens && \
    useradd --uid 1000 --gid civiclens --shell /usr/sbin/nologin --create-home civiclens

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy application source
COPY --chown=civiclens:civiclens api/ api/
COPY --chown=civiclens:civiclens main.py .
COPY --chown=civiclens:civiclens summary_v2.py* ./
COPY --chown=civiclens:civiclens probe_clips.py* ./
COPY --chown=civiclens:civiclens download_docs.py* ./

# Create data directory (optionally pre-populated via volume mount at runtime)
RUN mkdir -p meetings_output/clips meetings_output/chroma_db \
    && chown -R civiclens:civiclens /app/meetings_output

# Environment defaults
ENV MEETINGS_OUTPUT_DIR=/app/meetings_output \
    LOG_LEVEL=INFO \
    CORS_ORIGINS="*" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Switch to non-root user
USER civiclens

# Use tini as PID 1 for proper signal handling, exec into uvicorn
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
