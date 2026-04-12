# -----------------------------------------------------------------------------
# CivicLens SaaS Platform - Outputs
# -----------------------------------------------------------------------------

output "ecr_repository_url" {
  description = "ECR repository URL for Docker images"
  value       = aws_ecr_repository.app.repository_url
}

output "app_runner_service_url" {
  description = "App Runner service URL (default domain)"
  value       = aws_apprunner_service.api.service_url
}

output "app_runner_service_arn" {
  description = "App Runner service ARN (for CI/CD deployments)"
  value       = aws_apprunner_service.api.arn
}

output "frontend_bucket_name" {
  description = "S3 bucket name for frontend assets"
  value       = aws_s3_bucket.frontend.id
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (for cache invalidation)"
  value       = aws_cloudfront_distribution.main.id
}

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain name"
  value       = aws_cloudfront_distribution.main.domain_name
}

output "site_url" {
  description = "Primary site URL"
  value       = "https://${var.domain_name}"
}

output "api_url" {
  description = "API URL"
  value       = "https://${var.api_subdomain}.${var.domain_name}"
}

output "nameservers" {
  description = "Route53 nameservers (configure at your registrar)"
  value       = aws_route53_zone.main.name_servers
}

output "secrets_manager_arn" {
  description = "Secrets Manager secret ARN"
  value       = aws_secretsmanager_secret.app_secrets.arn
}
