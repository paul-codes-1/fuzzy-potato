#!/bin/bash
# End-to-end ingest and deploy for the latest LFUCG meetings.
#
# Pipeline:
#   1. Probe Granicus for new clip IDs (stops after 5 consecutive 404s)
#   2. Download + transcribe + v2-summarize any new clips (--auto --rag)
#   3. Backfill minutes/agenda for clips that were missing them (+ regenerate summaries)
#   4. Back-stop: upgrade any clips still missing v2 summaries
#   5. Back-stop: ingest any new clips into ChromaDB
#   6. Build frontend, sync to S3, invalidate CloudFront
#   7. Build + push Docker image, trigger App Runner redeployment
#   8. Ping feeds.lexingtonky.news so it pulls the new archive items

set -euo pipefail

# Disable AWS CLI pager so commands don't block on `less` waiting for `q`.
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Pull FEEDS_API_TOKEN / FEEDS_WEBHOOK_URL from .env if not already in env
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-861476138515}"
ECR_REPO="${ECR_REPO:-lfucg-rag-api}"
APP_RUNNER_SERVICE_NAME="${APP_RUNNER_SERVICE_NAME:-lfucg-rag-api}"
export CLOUDFRONT_DISTRIBUTION_ID="${CLOUDFRONT_DISTRIBUTION_ID:-E8OIXOXDRETLZ}"
FEEDS_WEBHOOK_URL="${FEEDS_WEBHOOK_URL:-https://feeds.lexingtonky.news/api/ingest/lfucg-meeting-archive}"
FEEDS_API_TOKEN="${FEEDS_API_TOKEN:-}"
MAX_CLIPS="${MAX_CLIPS:-}"
MAX_BACKFILL="${MAX_BACKFILL:-}"
SKIP_DEPLOY="${SKIP_DEPLOY:-0}"
SKIP_RAG_DEPLOY="${SKIP_RAG_DEPLOY:-0}"
SKIP_FEEDS_WEBHOOK="${SKIP_FEEDS_WEBHOOK:-0}"
SKIP_BACKFILL="${SKIP_BACKFILL:-0}"
SKIP_UPGRADE_SUMMARIES="${SKIP_UPGRADE_SUMMARIES:-0}"

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"

log() {
  echo ""
  echo "==================================================================="
  echo "==> [$(date +%H:%M:%S)] $*"
  echo "==================================================================="
}

log "Step 1/7: Probing for new clips"
uv run python probe_clips.py

log "Step 2/7: Processing new clips (--auto --rag --no-audio)"
AUTO_ARGS=(--auto --rag --no-audio)
if [ -n "$MAX_CLIPS" ]; then
  AUTO_ARGS+=(--max "$MAX_CLIPS")
fi
uv run python main.py "${AUTO_ARGS[@]}"

if [ "$SKIP_BACKFILL" = "1" ]; then
  log "Skipping Step 3 (backfill-docs) — SKIP_BACKFILL=1"
else
  log "Step 3/7: Backfilling minutes/agenda for existing clips"
  BACKFILL_ARGS=(--backfill-docs --regenerate-summary)
  if [ -n "$MAX_BACKFILL" ]; then
    BACKFILL_ARGS+=(--max "$MAX_BACKFILL")
  fi
  uv run python main.py "${BACKFILL_ARGS[@]}"
fi

if [ "$SKIP_UPGRADE_SUMMARIES" = "1" ]; then
  log "Skipping Step 4 (upgrade-summaries) — SKIP_UPGRADE_SUMMARIES=1"
else
  log "Step 4/7: Upgrading any clips missing v2 summaries"
  uv run python main.py --upgrade-summaries --max 9999
fi

log "Step 5/7: Ingesting new clips into ChromaDB"
uv run python -m rag.ingest --new

if [ "$SKIP_DEPLOY" = "1" ]; then
  log "Skipping frontend + RAG deploy (SKIP_DEPLOY=1)"
  exit 0
fi

log "Step 6/7: Building frontend and syncing to S3"
./deploy.sh

if [ "$SKIP_RAG_DEPLOY" = "1" ]; then
  log "Skipping RAG deploy (SKIP_RAG_DEPLOY=1)"
  exit 0
fi

log "Step 7/7: Deploying RAG API (Docker build + push + App Runner)"

echo "==> Logging into ECR ($ECR_URI)"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "==> Building image (linux/amd64)"
docker build --platform linux/amd64 -t "$ECR_REPO" .

echo "==> Tagging and pushing to $ECR_URI:latest"
docker tag "${ECR_REPO}:latest" "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"

echo "==> Looking up App Runner service ARN for '$APP_RUNNER_SERVICE_NAME'"
SERVICE_ARN=$(aws apprunner list-services --region "$AWS_REGION" \
  --query "ServiceSummaryList[?ServiceName==\`${APP_RUNNER_SERVICE_NAME}\`].ServiceArn" \
  --output text)

if [ -z "$SERVICE_ARN" ] || [ "$SERVICE_ARN" = "None" ]; then
  echo "ERROR: Could not find App Runner service named '$APP_RUNNER_SERVICE_NAME'" >&2
  exit 1
fi

echo "==> Triggering App Runner redeployment: $SERVICE_ARN"
aws apprunner start-deployment --region "$AWS_REGION" --service-arn "$SERVICE_ARN" \
  --query 'OperationId' --output text

if [ "$SKIP_FEEDS_WEBHOOK" = "1" ]; then
  log "Skipping feeds webhook (SKIP_FEEDS_WEBHOOK=1)"
elif [ -z "$FEEDS_API_TOKEN" ]; then
  log "Skipping feeds webhook (FEEDS_API_TOKEN not set)"
else
  log "Step 8: Pinging feeds.lexingtonky.news to pull new archive items"
  echo "==> POST $FEEDS_WEBHOOK_URL"
  http_code=$(curl -sS -o /tmp/feeds_webhook_response.json -w "%{http_code}" \
    -X POST \
    -H "Authorization: Bearer $FEEDS_API_TOKEN" \
    -H "Content-Type: application/json" \
    "$FEEDS_WEBHOOK_URL" || true)
  echo "==> HTTP $http_code"
  cat /tmp/feeds_webhook_response.json 2>/dev/null || true
  echo ""
  if [ "$http_code" != "200" ]; then
    echo "WARNING: feeds webhook returned $http_code (deploy already finished, so this is non-fatal)"
  fi
fi

log "All done. Frontend + RAG deploy kicked off."
echo "App Runner deployments typically take 2-5 minutes to complete."
