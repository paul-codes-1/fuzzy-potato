# -----------------------------------------------------------------------------
# CivicLens - Production Environment
# -----------------------------------------------------------------------------
# Usage: terraform apply -var-file=environments/production.tfvars

environment = "production"
domain_name = "civiclens.app"
app_name    = "civiclens"
aws_region  = "us-east-1"

# App Runner - production-grade instances
app_runner_cpu    = "2048"  # 2 vCPU
app_runner_memory = "4096"  # 4 GB

# Auto-scaling
autoscaling_min_size        = 1
autoscaling_max_size        = 5
autoscaling_max_concurrency = 100
