resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/lambda/${local.name}-pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.name}-pipeline-dlq"
  message_retention_seconds = 14 * 24 * 3600
  sqs_managed_sse_enabled   = true

  tags = { Name = "${local.name}-pipeline-dlq" }
}

# One function, dispatched on the event: Step Functions sends
# {"stage": ..., "payload": ...}; EventBridge Scheduler sends {"job": ...}.
resource "aws_lambda_function" "pipeline" {
  function_name = "${local.name}-pipeline"
  description   = "AIStora data pipeline: ingest stages, nightly jobs and connectors."
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.pipeline.repository_url}:${var.image_tag}"
  architectures = [var.lambda_architecture]

  memory_size = var.lambda_memory_mb
  timeout     = 900

  reserved_concurrent_executions = var.lambda_reserved_concurrency

  ephemeral_storage {
    size = 10240
  }

  environment {
    variables = {
      PIPELINE_BACKEND       = "aws"
      LAKE_BUCKET            = aws_s3_bucket.lake.id
      ICEBERG_CATALOG        = "glue"
      ICEBERG_NAMESPACE      = aws_glue_catalog_database.lake.name
      PIPELINE_SCRATCH_DIR   = "/tmp/aistora-pipeline"
      PIPELINE_MAX_RAW_BYTES = tostring(var.max_raw_bytes)
      LOG_LEVEL              = var.log_level
    }
  }

  # Asynchronous invocations (Scheduler jobs) that fail every retry land here.
  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.pipeline.name
  }

  # The workflow rolls out new images with update-function-code; Terraform
  # must not roll them back to var.image_tag on the next apply.
  lifecycle {
    ignore_changes = [image_uri]
  }

  depends_on = [
    aws_cloudwatch_log_group.pipeline,
    aws_iam_role_policy_attachment.lambda_basic,
    aws_iam_role_policy.lambda,
  ]

  tags = { Name = "${local.name}-pipeline" }
}

resource "aws_lambda_function_event_invoke_config" "pipeline" {
  function_name                = aws_lambda_function.pipeline.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 6 * 3600
}
