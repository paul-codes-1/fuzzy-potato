# -----------------------------------------------------------------------------
# CivicLens - Staging Environment
# -----------------------------------------------------------------------------
# Usage: terraform apply -var-file=environments/staging.tfvars

environment = "staging"
domain_name = "staging.civiclens.app"
app_name    = "civiclens"
aws_region  = "us-east-1"

# App Runner - smaller instances for staging
app_runner_cpu    = "1024"  # 1 vCPU
app_runner_memory = "2048"  # 2 GB

# Auto-scaling - minimal for staging
autoscaling_min_size        = 1
autoscaling_max_size        = 2
autoscaling_max_concurrency = 50
