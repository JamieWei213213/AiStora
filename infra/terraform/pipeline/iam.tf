# --- Pipeline Lambda ---------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.name}-pipeline-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# No VPC: S3, Glue and SSM are reached over their public endpoints from the
# Lambda service network, so there is no NAT gateway to pay for.
data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "ListLake"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lake.arn]
  }

  # Plain s3:PutObject covers the conditional (If-None-Match) writes the
  # lock and manifest code relies on.
  statement {
    sid    = "ManageLakeObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
  }

  statement {
    sid    = "IcebergCatalog"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:CreateDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:DeleteTable",
    ]
    resources = [
      "arn:${local.partition}:glue:${var.aws_region}:${local.account_id}:catalog",
      aws_glue_catalog_database.lake.arn,
      "arn:${local.partition}:glue:${var.aws_region}:${local.account_id}:table/${aws_glue_catalog_database.lake.name}/*",
    ]
  }

  # Connector secrets: SecureString parameters under /<name>/connectors/
  # encrypted with the AWS-managed aws/ssm key, which needs no KMS grant.
  statement {
    sid       = "ReadConnectorSecrets"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:${local.partition}:ssm:${var.aws_region}:${local.account_id}:parameter${local.connector_parameter_path}/*"]
  }

  statement {
    sid       = "DeadLetter"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.dlq.arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "pipeline-lake"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

# --- Step Functions -----------------------------------------------------------

data "aws_iam_policy_document" "states_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "state_machine" {
  name               = "${local.name}-ingest-states"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
}

data "aws_iam_policy_document" "state_machine" {
  statement {
    sid       = "InvokePipeline"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.pipeline.arn, "${aws_lambda_function.pipeline.arn}:*"]
  }

  # CloudWatch Logs delivery for Step Functions requires these on "*".
  statement {
    sid    = "DeliverLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "state_machine" {
  name   = "ingest"
  role   = aws_iam_role.state_machine.id
  policy = data.aws_iam_policy_document.state_machine.json
}

# --- EventBridge rule target -------------------------------------------------

data "aws_iam_policy_document" "events_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events" {
  name               = "${local.name}-ingest-events"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

data "aws_iam_policy_document" "events" {
  statement {
    sid       = "StartIngest"
    effect    = "Allow"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.ingest.arn]
  }
}

resource "aws_iam_role_policy" "events" {
  name   = "start-ingest"
  role   = aws_iam_role.events.id
  policy = data.aws_iam_policy_document.events.json
}

# --- EventBridge Scheduler ---------------------------------------------------

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-pipeline-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "InvokePipeline"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.pipeline.arn, "${aws_lambda_function.pipeline.arn}:*"]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "invoke-pipeline"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

# --- Dead-letter queue policy ---------------------------------------------------

data "aws_iam_policy_document" "dlq" {
  statement {
    sid     = "EventBridgeAndSchedulerDeadLetters"
    effect  = "Allow"
    actions = ["sqs:SendMessage"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com", "scheduler.amazonaws.com"]
    }

    resources = [aws_sqs_queue.dlq.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  policy    = data.aws_iam_policy_document.dlq.json
}

# --- GitHub Actions deploy permissions ----------------------------------------

data "aws_iam_role" "github_deploy" {
  count = var.github_deploy_role_name == null ? 0 : 1

  name = var.github_deploy_role_name
}

data "aws_iam_policy_document" "github_deploy" {
  count = var.github_deploy_role_name == null ? 0 : 1

  statement {
    sid       = "EcrLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PushPipelineImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.pipeline.arn]
  }

  statement {
    sid    = "UpdatePipelineFunction"
    effect = "Allow"
    actions = [
      "lambda:GetFunction",
      "lambda:GetFunctionConfiguration",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
    ]
    resources = [aws_lambda_function.pipeline.arn]
  }

  statement {
    sid    = "SmokeTestExecutions"
    effect = "Allow"
    actions = [
      "states:StartExecution",
      "states:DescribeExecution",
    ]
    resources = [
      aws_sfn_state_machine.ingest.arn,
      "arn:${local.partition}:states:${var.aws_region}:${local.account_id}:execution:${aws_sfn_state_machine.ingest.name}:*",
    ]
  }

  # The smoke test drops a file for project 0 and reads its manifest back.
  statement {
    sid       = "SmokeTestDrop"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/raw/project=0/*"]
  }

  statement {
    sid       = "SmokeTestManifest"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/manifests/project=0/*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count = var.github_deploy_role_name == null ? 0 : 1

  name   = "deploy-pipeline-only"
  role   = data.aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}
