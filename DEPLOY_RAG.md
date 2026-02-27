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

## Step 3: Store OpenAI API Key

```bash
# Store API key securely in Secrets Manager
aws secretsmanager create-secret \
  --name lfucg-openai-key \
  --secret-string '{"OPENAI_API_KEY":"sk-..."}'
```

## Step 4: Create IAM Roles

### 4a. Instance Role (S3 + Secrets Manager access)

```bash
# Create role with trust policy
aws iam create-role --role-name AppRunnerS3ReadRole \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "tasks.apprunner.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

# Attach S3 and Secrets Manager permissions
aws iam put-role-policy --role-name AppRunnerS3ReadRole \
  --policy-name S3SecretsAccess \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": ["s3:GetObject", "s3:ListBucket"],
        "Resource": [
          "arn:aws:s3:::public-meetings",
          "arn:aws:s3:::public-meetings/data/*"
        ]
      },
      {
        "Effect": "Allow",
        "Action": "secretsmanager:GetSecretValue",
        "Resource": "arn:aws:secretsmanager:us-east-1:<ACCOUNT_ID>:secret:lfucg-openai-key-*"
      }
    ]
  }'
```

### 4b. ECR Access Role

```bash
# Create role with trust policy
aws iam create-role --role-name AppRunnerECRAccess \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "build.apprunner.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

# Attach ECR permissions
aws iam attach-role-policy --role-name AppRunnerECRAccess \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
```

## Step 5: Create App Runner Service

```bash
# Get the secret ARN
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id lfucg-openai-key --query ARN --output text)

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
          "S3_BUCKET": "public-meetings",
          "S3_DATA_PREFIX": "data/",
          "LFUCG_OUTPUT_DIR": "/app/lfucg_output"
        },
        "RuntimeEnvironmentSecrets": {
          "OPENAI_API_KEY": "'"$SECRET_ARN"':OPENAI_API_KEY::"
        }
      }
    }
  }' \
  --instance-configuration '{
    "Cpu": "1 vCPU",
    "Memory": "2 GB",
    "InstanceRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/AppRunnerS3ReadRole"
  }' \
  --health-check-configuration '{
    "Protocol": "HTTP",
    "Path": "/health",
    "Interval": 20,
    "Timeout": 10,
    "HealthyThreshold": 1,
    "UnhealthyThreshold": 3
  }'
```

**Note:** Increased memory to 2 GB and health check timeout to 10s to handle ChromaDB loading on cold starts.

Wait ~5 minutes for the service to deploy.

## Step 6: Verify App Runner

```bash
# Get the service URL
aws apprunner list-services --query 'ServiceSummaryList[?ServiceName==`lfucg-rag-api`].ServiceUrl' --output text

# Test health (note: /health not /api/health for direct App Runner access)
curl https://<app-runner-url>/health

# Test a query
curl -X POST https://<app-runner-url>/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What has the city discussed about short-term rentals?"}'
```

## Step 7: Add CloudFront Origin for App Runner

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

## Step 8: Test End-to-End

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
