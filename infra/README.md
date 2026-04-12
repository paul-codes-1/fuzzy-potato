# CivicLens Infrastructure

Terraform configuration for deploying the CivicLens SaaS platform on AWS.

## Architecture

```
                         +------------------+
                         |    Route 53      |
                         | civiclens.app    |
                         | api.civiclens.app|
                         +--------+---------+
                                  |
                         +--------+---------+
                         |   CloudFront     |
                         |  (HTTPS + CDN)   |
                         +---+----------+---+
                             |          |
                    /static  |          | /api/*
                             |          |
                   +---------+--+  +----+---------+
                   | S3 Bucket  |  | App Runner   |
                   | (Frontend) |  | (FastAPI)    |
                   | React SPA  |  |              |
                   +------------+  | +----------+ |
                                   | | SQLite   | |
                                   | | ChromaDB | |
                                   | +----------+ |
                                   +------+-------+
                                          |
                                   +------+-------+
                                   |   ECR        |
                                   | Docker images|
                                   +--------------+

        Secrets Manager              ACM Certificate
        (API keys)                   (TLS for *.civiclens.app)
```

## Prerequisites

1. **Terraform >= 1.5** installed
2. **AWS CLI** configured with credentials
3. **S3 bucket + DynamoDB table** for Terraform state (one-time manual setup):

```bash
# Create state bucket
aws s3api create-bucket \
  --bucket civiclens-terraform-state \
  --region us-east-1

aws s3api put-bucket-versioning \
  --bucket civiclens-terraform-state \
  --versioning-configuration Status=Enabled

# Create lock table
aws dynamodb create-table \
  --table-name civiclens-terraform-lock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

## Deploy

### Staging

```bash
cd infra

# Initialize with staging backend
terraform init -backend-config=environments/staging-backend.hcl

# Plan
terraform plan -var-file=environments/staging.tfvars

# Apply
terraform apply -var-file=environments/staging.tfvars
```

### Production

```bash
cd infra

# Initialize with production backend (use -reconfigure if switching)
terraform init -backend-config=environments/production-backend.hcl -reconfigure

# Plan
terraform plan -var-file=environments/production.tfvars

# Apply
terraform apply -var-file=environments/production.tfvars
```

### Switching Environments

When switching between staging and production backends:

```bash
terraform init -backend-config=environments/production-backend.hcl -reconfigure
```

## Post-Deploy Setup

### 1. Set Secrets

After first deploy, update the placeholder secrets:

```bash
aws secretsmanager put-secret-value \
  --secret-id civiclens-production/api-keys \
  --secret-string '{
    "OPENAI_API_KEY": "sk-...",
    "ANTHROPIC_API_KEY": "sk-ant-...",
    "STRIPE_SECRET_KEY": "sk_live_...",
    "ADMIN_API_KEY": "your-admin-key"
  }'
```

### 2. Configure Domain Nameservers

After deploy, get the Route53 nameservers:

```bash
terraform output nameservers
```

Update your domain registrar to point to these nameservers.

### 3. Push Docker Image

```bash
# Get ECR URL
ECR_URL=$(terraform output -raw ecr_repository_url)

# Login, build, push
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$ECR_URL"
docker build -t "$ECR_URL:latest" ..
docker push "$ECR_URL:latest"
```

### 4. Deploy Frontend

```bash
BUCKET=$(terraform output -raw frontend_bucket_name)
DIST_ID=$(terraform output -raw cloudfront_distribution_id)

# Build and upload
cd ../frontend && npm run build
aws s3 sync dist/ "s3://$BUCKET/" --delete

# Invalidate CloudFront cache
aws cloudfront create-invalidation --distribution-id "$DIST_ID" --paths "/*"
```

## Cost Estimates (Monthly)

| Resource | Staging | Production |
|----------|---------|------------|
| App Runner (1 vCPU, 2GB, 1 instance) | ~$29 | - |
| App Runner (2 vCPU, 4GB, 1-5 instances) | - | ~$58-290 |
| ECR (10 images, ~2GB) | ~$0.20 | ~$0.20 |
| S3 (frontend, <1GB) | ~$0.03 | ~$0.03 |
| CloudFront (10GB/mo transfer) | ~$0.85 | ~$0.85 |
| Route53 (hosted zone) | $0.50 | $0.50 |
| Secrets Manager (4 secrets) | $1.60 | $1.60 |
| ACM Certificate | Free | Free |
| **Total (idle)** | **~$32** | **~$61** |

App Runner charges per vCPU-hour and GB-hour only while instances are active. With auto-scaling, production costs scale with traffic.

## Destroying Infrastructure

```bash
# Staging (force_destroy enabled on S3/ECR)
terraform destroy -var-file=environments/staging.tfvars

# Production (must empty S3 bucket first)
aws s3 rm s3://civiclens-production-frontend --recursive
terraform destroy -var-file=environments/production.tfvars
```
