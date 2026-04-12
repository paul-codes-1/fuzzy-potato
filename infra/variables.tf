# -----------------------------------------------------------------------------
# CivicLens SaaS Platform - Variables
# -----------------------------------------------------------------------------

variable "environment" {
  description = "Deployment environment (staging or production)"
  type        = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Environment must be 'staging' or 'production'."
  }
}

variable "app_name" {
  description = "Application name used in resource naming"
  type        = string
  default     = "civiclens"
}

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "domain_name" {
  description = "Root domain name (e.g. civiclens.app)"
  type        = string
}

variable "api_subdomain" {
  description = "Subdomain for the API (e.g. api -> api.civiclens.app)"
  type        = string
  default     = "api"
}

# -----------------------------------------------------------------------------
# App Runner Configuration
# -----------------------------------------------------------------------------

variable "app_runner_cpu" {
  description = "App Runner vCPU units (1024 = 1 vCPU, 2048 = 2 vCPU)"
  type        = string
  default     = "1024"
}

variable "app_runner_memory" {
  description = "App Runner memory in MB (2048, 3072, 4096)"
  type        = string
  default     = "2048"
}

variable "app_runner_port" {
  description = "Container port the FastAPI app listens on"
  type        = number
  default     = 8000
}

variable "health_check_path" {
  description = "Health check endpoint path"
  type        = string
  default     = "/api/health"
}

variable "health_check_interval" {
  description = "Seconds between health checks"
  type        = number
  default     = 10
}

# -----------------------------------------------------------------------------
# Auto-Scaling
# -----------------------------------------------------------------------------

variable "autoscaling_min_size" {
  description = "Minimum number of App Runner instances"
  type        = number
  default     = 1
}

variable "autoscaling_max_size" {
  description = "Maximum number of App Runner instances"
  type        = number
  default     = 3
}

variable "autoscaling_max_concurrency" {
  description = "Max concurrent requests per instance before scaling"
  type        = number
  default     = 50
}

# -----------------------------------------------------------------------------
# Terraform State Backend (set via -backend-config or environment)
# -----------------------------------------------------------------------------

variable "terraform_state_bucket" {
  description = "S3 bucket for Terraform state (must be created manually)"
  type        = string
  default     = "civiclens-terraform-state"
}

variable "terraform_state_lock_table" {
  description = "DynamoDB table for Terraform state locking"
  type        = string
  default     = "civiclens-terraform-lock"
}

# -----------------------------------------------------------------------------
# Tags
# -----------------------------------------------------------------------------

variable "extra_tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
