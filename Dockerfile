FROM python:3.11-slim

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY rag/ rag/
COPY main.py .

# Install project with rag dependencies
RUN pip install --no-cache-dir ".[rag]"

# Copy baked-in data (chroma_db + clip metadata)
COPY lfucg_output/chroma_db/ lfucg_output/chroma_db/
COPY lfucg_output/clips/ lfucg_output/clips/

# Copy entrypoint
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENV LFUCG_OUTPUT_DIR=/app/lfucg_output

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
