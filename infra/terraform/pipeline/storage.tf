resource "aws_s3_bucket" "lake" {
  bucket        = "${local.name}-${local.account_id}-${var.aws_region}-lake"
  force_destroy = var.lake_force_destroy

  tags = { Name = "${local.name}-lake" }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket = aws_s3_bucket.lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning stays off on purpose: Iceberg keeps its own snapshots under
# iceberg/, and S3 versions of every rewritten Parquet file would double the
# storage bill without adding a recovery path the catalog does not already
# provide. Declaring it keeps the intent visible in the plan.
resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id

  versioning_configuration {
    status = "Disabled"
  }
}

# Prefix layout: see pipeline/keys.py. Anything not listed here is durable.
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "expire-work"
    status = "Enabled"

    filter {
      prefix = "work/"
    }

    expiration {
      days = 1
    }
  }

  rule {
    id     = "expire-quarantine"
    status = "Enabled"

    filter {
      prefix = "quarantine/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "archive-raw"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }
  }

  # Silver is a per-load staging artifact; the curated Iceberg table is the
  # durable layer.
  rule {
    id     = "expire-silver"
    status = "Enabled"

    filter {
      prefix = "silver/"
    }

    expiration {
      days = 60
    }
  }

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
  }

  depends_on = [aws_s3_bucket_versioning.lake]
}

data "aws_iam_policy_document" "lake_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.lake.arn,
      "${aws_s3_bucket.lake.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket = aws_s3_bucket.lake.id
  policy = data.aws_iam_policy_document.lake_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.lake]
}

# Every object event goes to the default event bus; the rule in events.tf
# narrows it to raw/ so the state machine starts once per uploaded file.
resource "aws_s3_bucket_notification" "lake" {
  bucket      = aws_s3_bucket.lake.id
  eventbridge = true
}

resource "aws_ecr_repository" "pipeline" {
  name                 = "${local.name}-pipeline"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "pipeline" {
  repository = aws_ecr_repository.pipeline.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_glue_catalog_database" "lake" {
  name        = local.glue_database_name
  description = "Iceberg namespace for ${local.name} curated datasets (tables p<project>__<dataset>)."

  tags = { Name = local.glue_database_name }
}
