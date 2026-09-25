# --- S3 object created under raw/ -> ingest state machine -----------------

resource "aws_cloudwatch_event_rule" "raw_object_created" {
  name        = "${local.name}-raw-object-created"
  description = "Start the ingest state machine for every object created under raw/ in the lake bucket."

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.lake.id] }
      object = { key = [{ prefix = "raw/" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "ingest" {
  rule     = aws_cloudwatch_event_rule.raw_object_created.name
  arn      = aws_sfn_state_machine.ingest.arn
  role_arn = aws_iam_role.events.arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 10
  }

  dead_letter_config {
    arn = aws_sqs_queue.dlq.arn
  }
}

# --- Scheduled jobs --------------------------------------------------------

resource "aws_scheduler_schedule_group" "pipeline" {
  name = "${local.name}-pipeline"

  tags = { Name = "${local.name}-pipeline" }
}

resource "aws_scheduler_schedule" "nightly_telemetry" {
  name        = "nightly-telemetry"
  group_name  = aws_scheduler_schedule_group.pipeline.name
  description = "Nightly dbt build of the gold telemetry marts."

  schedule_expression          = "cron(0 3 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ job = "telemetry" })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }

    dead_letter_config {
      arn = aws_sqs_queue.dlq.arn
    }
  }
}

resource "aws_scheduler_schedule" "nightly_compaction" {
  name        = "nightly-compaction"
  group_name  = aws_scheduler_schedule_group.pipeline.name
  description = "Nightly small-file compaction and Iceberg snapshot expiry."

  schedule_expression          = "cron(30 3 * * ? *)"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ job = "compaction" })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }

    dead_letter_config {
      arn = aws_sqs_queue.dlq.arn
    }
  }
}

resource "aws_scheduler_schedule" "connector" {
  for_each = var.connector_schedules

  name        = "connector-${each.key}"
  group_name  = aws_scheduler_schedule_group.pipeline.name
  description = "Run the ${each.key} connector."

  schedule_expression          = each.value
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ job = "connector", connector_id = each.key })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }

    dead_letter_config {
      arn = aws_sqs_queue.dlq.arn
    }
  }
}
