FROM python:3.11-slim

WORKDIR /app

# Install awscli for S3 sync at startup
RUN pip install --no-cache-dir awscli boto3

# Copy project files
COPY pyproject.toml .
COPY rag/ rag/
COPY main.py .

# Install project with rag dependencies
RUN pip install --no-cache-dir ".[rag]"

# Copy entrypoint
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
