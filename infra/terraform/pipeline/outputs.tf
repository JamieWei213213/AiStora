output "lake_bucket" {
  description = "Lake bucket. Pass it to the application stack as lake_bucket_name."
  value       = aws_s3_bucket.lake.id
}

output "pipeline_function_name" {
  description = "Pipeline Lambda name. Pass it to the application stack as pipeline_function_name."
  value       = aws_lambda_function.pipeline.function_name
}

output "pipeline_function_arn" {
  value = aws_lambda_function.pipeline.arn
}

output "state_machine_arn" {
  description = "Ingest state machine; one execution per object created under raw/."
  value       = aws_sfn_state_machine.ingest.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.pipeline.repository_url
}

output "glue_database" {
  description = "Glue database (Iceberg namespace). Pass it to the application stack as glue_database_name."
  value       = aws_glue_catalog_database.lake.name
}

output "sns_topic_arn" {
  description = "Alarm topic. Subscribe additional endpoints out of band."
  value       = aws_sns_topic.alarms.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.id
}

output "dashboard_name" {
  value = aws_cloudwatch_dashboard.pipeline.dashboard_name
}

output "connector_parameter_path" {
  description = "SSM path under which connector secret_ref parameters must be created."
  value       = local.connector_parameter_path
}

output "github_actions_variables" {
  description = "Non-secret repository variables required by .github/workflows/pipeline.yml."
  value = {
    AWS_REGION                 = var.aws_region
    PIPELINE_ECR_REPOSITORY    = aws_ecr_repository.pipeline.name
    PIPELINE_FUNCTION_NAME     = aws_lambda_function.pipeline.function_name
    PIPELINE_STATE_MACHINE_ARN = aws_sfn_state_machine.ingest.arn
    LAKE_BUCKET                = aws_s3_bucket.lake.id
    PIPELINE_GLUE_DATABASE     = aws_glue_catalog_database.lake.name
  }
}
