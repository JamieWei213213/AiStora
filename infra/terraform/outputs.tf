output "application_url" {
  description = "Public load balancer URL. Add an ACM certificate for HTTPS."
  value = (
    var.certificate_arn == null
    ? "http://${aws_lb.app.dns_name}"
    : "https://${aws_lb.app.dns_name}"
  )
}

output "dataset_bucket" {
  description = "Private S3 bucket used for durable dataset storage."
  value       = aws_s3_bucket.datasets.id
}

output "database_endpoint" {
  description = "Private RDS endpoint; it is reachable only from the ECS security group."
  value       = aws_db_instance.main.endpoint
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "github_deploy_role_arn" {
  description = "Set this as the GitHub repository variable AWS_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.github_deploy.arn
}

output "github_actions_variables" {
  description = "Non-secret repository variables required by the deployment workflow."
  value = {
    AWS_REGION                 = var.aws_region
    ECR_REPOSITORY             = aws_ecr_repository.app.name
    ECS_CLUSTER                = aws_ecs_cluster.main.name
    ECS_SERVICE                = aws_ecs_service.app.name
    ECS_TASK_DEFINITION_FAMILY = aws_ecs_task_definition.app.family
    APP_URL                    = var.certificate_arn == null ? "http://${aws_lb.app.dns_name}" : "https://${aws_lb.app.dns_name}"
    AWS_DEPLOY_ROLE_ARN        = aws_iam_role.github_deploy.arn
  }
}

output "gemini_secret_arn" {
  description = "Update this secret value out of band; never put the API key in Terraform variables."
  value       = aws_secretsmanager_secret.gemini.arn
}

output "session_cache_endpoint" {
  description = "Redis endpoint for REDIS_URL, or null when the cache is disabled."
  value       = try(aws_elasticache_replication_group.sessions[0].primary_endpoint_address, null)
}
