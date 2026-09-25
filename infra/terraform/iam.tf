data "aws_iam_policy_document" "ecs_task_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json
}

data "aws_iam_policy_document" "task_storage" {
  statement {
    sid    = "ManageDatasetObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.datasets.arn}/datasets/*"]
  }
}

resource "aws_iam_role_policy" "task_storage" {
  name   = "dataset-storage"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_storage.json
}

# Lake access (infra/terraform/pipeline). Prefixes follow pipeline/keys.py:
# the app writes uploads to raw/, reads curated Parquet and manifests, and
# rolls Iceberg tables back through the Glue catalog.
locals {
  lake_enabled    = var.lake_bucket_name != null
  lake_bucket_arn = "arn:aws:s3:::${coalesce(var.lake_bucket_name, "unset")}"
}

data "aws_iam_policy_document" "task_lake" {
  count = local.lake_enabled ? 1 : 0

  statement {
    sid       = "ListLakePrefixes"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [local.lake_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "manifests/*",
        "curated/*",
        "silver/*",
        "contracts/*",
        "connectors/*",
        "gold/*",
        "events/*",
        "raw/*",
        "locks/*",
      ]
    }
  }

  statement {
    sid     = "ReadLakeObjects"
    effect  = "Allow"
    actions = ["s3:GetObject"]
    resources = [
      for prefix in ["manifests", "curated", "silver", "contracts", "connectors", "gold", "locks", "quarantine"] :
      "${local.lake_bucket_arn}/${prefix}/*"
    ]
  }

  statement {
    sid     = "WriteLakeObjects"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      for prefix in ["raw", "manifests", "events", "connectors", "curated", "locks"] :
      "${local.lake_bucket_arn}/${prefix}/*"
    ]
  }

  statement {
    sid     = "DeleteLakeObjects"
    effect  = "Allow"
    actions = ["s3:DeleteObject"]
    resources = [
      for prefix in ["connectors", "locks"] :
      "${local.lake_bucket_arn}/${prefix}/*"
    ]
  }

  dynamic "statement" {
    for_each = var.glue_database_name == null ? [] : [var.glue_database_name]

    content {
      sid    = "IcebergCatalog"
      effect = "Allow"
      actions = [
        "glue:GetDatabase",
        "glue:GetDatabases",
        "glue:GetTable",
        "glue:GetTables",
        "glue:CreateTable",
        "glue:UpdateTable",
      ]
      resources = [
        "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
        "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${statement.value}",
        "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${statement.value}/*",
      ]
    }
  }

  dynamic "statement" {
    for_each = var.pipeline_function_name == null ? [] : [var.pipeline_function_name]

    content {
      sid       = "TriggerPipelineJobs"
      effect    = "Allow"
      actions   = ["lambda:InvokeFunction"]
      resources = ["arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${statement.value}"]
    }
  }
}

resource "aws_iam_role_policy" "task_lake" {
  count = local.lake_enabled ? 1 : 0

  name   = "lake-access"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_lake[0].json
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid     = "ReadOnlyRuntimeSecrets"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.flask.arn,
      aws_secretsmanager_secret.gemini.arn,
      aws_db_instance.main.master_user_secret[0].secret_arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "runtime-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.github_oidc_provider_arn == null ? 1 : 0

  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]
}

locals {
  github_oidc_provider_arn = coalesce(
    var.github_oidc_provider_arn,
    try(aws_iam_openid_connect_provider.github[0].arn, null),
  )
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "${local.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "EcrLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PushApplicationImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    sid    = "RegisterAndReadTaskDefinitions"
    effect = "Allow"
    actions = [
      "ecs:DescribeTaskDefinition",
      "ecs:ListTaskDefinitions",
      "ecs:RegisterTaskDefinition",
      "ecs:TagResource",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "DeployOnlyAIStoraService"
    effect = "Allow"
    actions = [
      "ecs:DescribeServices",
      "ecs:UpdateService",
    ]
    resources = [
      "arn:aws:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}",
    ]
  }

  statement {
    sid     = "PassOnlyAIStoraTaskRoles"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.task.arn,
      aws_iam_role.execution.arn,
    ]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "deploy-aistora-only"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}
