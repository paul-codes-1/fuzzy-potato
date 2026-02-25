# Deploy ChatLFUCG RAG API on AWS App Runner

## Prerequisites

```bash
# Set AWS profile and region
awsp personal
awsr us-east-1
```

## Architecture

```
Browser → CloudFront → /api/* behavior → App Runner (FastAPI)
                     → /* behavior    → S3 (frontend)

App Runner container startup:
  1. Syncs chroma_db/ and clips/*/metadata.json from S3
  2. Starts uvicorn rag.server:app on port 8000
```

CloudFront routes `/api/*` to App Runner, everything else to S3. Same domain means no CORS issues, no frontend code changes needed.

---

## Step 1: Create ECR Repository

```bash
aws ecr create-repository --repository-name lfucg-rag-api --region us-east-1
```

Save the `repositoryUri` from the output (e.g., `123456789.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api`).

## Step 2: Build & Push Docker Image

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# Build
docker build -t lfucg-rag-api .

# Tag
docker tag lfucg-rag-api:latest <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api:latest

# Push
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api:latest
```

## Step 3: Create IAM Roles

### 3a. Instance Role (S3 read access)

Create role `AppRunnerS3ReadRole` with trust policy for `tasks.apprunner.amazonaws.com`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::public-meetings",
      "arn:aws:s3:::public-meetings/data/*"
    ]
  }]
}
```

### 3b. ECR Access Role

Create role `AppRunnerECRAccess` with trust policy for `build.apprunner.amazonaws.com`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetAuthorizationToken",
      "ecr:DescribeImages"
    ],
    "Resource": "*"
  }]
}
```

## Step 4: Create App Runner Service

```bash
aws apprunner create-service \
  --service-name lfucg-rag-api \
  --source-configuration '{
    "AuthenticationConfiguration": {
      "AccessRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/AppRunnerECRAccess"
    },
    "ImageRepository": {
      "ImageIdentifier": "<ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api:latest",
      "ImageRepositoryType": "ECR",
      "ImageConfiguration": {
        "Port": "8000",
        "RuntimeEnvironmentVariables": {
          "OPENAI_API_KEY": "<your-key>",
          "S3_BUCKET": "public-meetings",
          "S3_DATA_PREFIX": "data/",
          "LFUCG_OUTPUT_DIR": "/app/lfucg_output"
        }
      }
    }
  }' \
  --instance-configuration '{
    "Cpu": "0.25 vCPU",
    "Memory": "0.5 GB",
    "InstanceRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/AppRunnerS3ReadRole"
  }' \
  --health-check-configuration '{
    "Protocol": "HTTP",
    "Path": "/api/health",
    "Interval": 10,
    "Timeout": 5,
    "HealthyThreshold": 1,
    "UnhealthyThreshold": 5
  }'
```

Wait ~5 minutes for the service to deploy.

## Step 5: Verify App Runner

```bash
# Get the service URL
aws apprunner list-services --query 'ServiceSummaryList[?ServiceName==`lfucg-rag-api`].ServiceUrl' --output text

# Test health
curl https://<app-runner-url>/api/health

# Test a query
curl -X POST https://<app-runner-url>/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What has the city discussed about short-term rentals?"}'
```

## Step 6: Add CloudFront Origin for App Runner

In the CloudFront distribution console:

### 6a. Add Origin

- **Origin domain:** `<random>.us-east-1.awsapprunner.com` (from Step 5)
- **Protocol:** HTTPS only
- **Name:** `rag-api`

### 6b. Add Behavior

- **Path pattern:** `/api/*`
- **Origin:** `rag-api`
- **Viewer protocol:** HTTPS only
- **Allowed HTTP methods:** GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE
- **Cache policy:** `CachingDisabled`
- **Origin request policy:** `AllViewerExceptHostHeader`
- **Function associations:** None (don't attach the basic auth function — browser sends the header automatically from the initial page auth)

### 6c. Invalidate Cache

```bash
aws cloudfront create-invalidation \
  --distribution-id $CLOUDFRONT_DISTRIBUTION_ID \
  --paths "/api/*"
```

## Step 7: Test End-to-End

1. Visit your CloudFront URL
2. Navigate to `/ask`
3. Ask a question — should hit CloudFront → App Runner → respond

---

## Updating the Deployment

When you process new clips and re-ingest into ChromaDB:

```bash
# 1. Sync chroma_db to S3
aws s3 sync lfucg_output/chroma_db/ s3://public-meetings/data/chroma_db/

# 2. Sync new clip metadata
aws s3 sync lfucg_output/clips/ s3://public-meetings/data/clips/ \
  --exclude "*" --include "*/metadata.json"

# 3. Restart App Runner to pick up new data
aws apprunner start-deployment --service-arn <SERVICE_ARN>
```

---

## Local Development

No changes — works exactly as before:

```bash
# Terminal 1: RAG server
uv run uvicorn rag.server:app --reload --port 8000

# Terminal 2: Frontend
cd frontend && npm run dev
# Vite proxies /api/* → localhost:8000
```
