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

Docker image bakes in chroma_db/ and clips/*/metadata.json (~1.4 GB).
No S3 sync at startup — uvicorn starts immediately.
ChromaDB loads lazily on first /ask request.
```

CloudFront routes `/api/*` to App Runner, everything else to S3. Same domain means no CORS issues, no frontend code changes needed.

---

## Step 1: Create ECR Repository

```bash
aws ecr create-repository --repository-name lfucg-rag-api --region us-east-1
```

Save the `repositoryUri` from the output (e.g., `123456789.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api`).

## Step 2: Build & Push Docker Image

**Important:** Use `--platform linux/amd64` — App Runner runs x86, and building on Apple Silicon without this flag produces ARM images that silently crash.

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# Build (must specify amd64 on Apple Silicon Macs)
docker build --platform linux/amd64 --no-cache -t lfucg-rag-api .

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

### 4a. ECR Access Role

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

### 4b. Instance Role (Secrets Manager access)

```bash
# Create role with trust policy
aws iam create-role --role-name AppRunnerInstanceRole \
  --assume-role-policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Principal": {"Service": "tasks.apprunner.amazonaws.com"},
      "Action": "sts:AssumeRole"
    }]
  }'

# Attach Secrets Manager permissions
aws iam put-role-policy --role-name AppRunnerInstanceRole \
  --policy-name SecretsAccess \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:<ACCOUNT_ID>:secret:lfucg-openai-key-*"
    }]
  }'
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
    "Memory": "4 GB",
    "InstanceRoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/AppRunnerInstanceRole"
  }' \
  --health-check-configuration '{
    "Protocol": "HTTP",
    "Path": "/health",
    "Interval": 20,
    "Timeout": 10,
    "HealthyThreshold": 1,
    "UnhealthyThreshold": 5
  }'
```

**Notes:**
- Memory set to 4 GB for ChromaDB (1.4 GB store + runtime overhead)
- Health check is lightweight (doesn't load ChromaDB) — passes immediately on startup
- ChromaDB loads lazily on first `/ask` request

Wait ~2-5 minutes for the service to deploy.

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

### 7a. Add Origin

- **Origin domain:** `<random>.us-east-1.awsapprunner.com` (from Step 6)
- **Protocol:** HTTPS only
- **Name:** `rag-api`

### 7b. Add Behavior

- **Path pattern:** `/api/*`
- **Origin:** `rag-api`
- **Viewer protocol:** HTTPS only
- **Allowed HTTP methods:** GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE
- **Cache policy:** `CachingDisabled`
- **Origin request policy:** `AllViewerExceptHostHeader`
- **Function associations:** None (don't attach the basic auth function — browser sends the header automatically from the initial page auth)

### 7c. Invalidate Cache

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
# 1. Rebuild image with latest data baked in
docker build --platform linux/amd64 --no-cache -t lfucg-rag-api .

# 2. Tag and push
docker tag lfucg-rag-api:latest <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api:latest
docker push <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/lfucg-rag-api:latest

# 3. Trigger redeployment
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
