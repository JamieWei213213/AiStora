terraform {
  required_version = ">= 1.8.0"

  # Separate state from the application stack: the lake, the catalog and the
  # state machine keep running while infra/terraform is destroyed and
  # recreated. The bucket, key and region come from backend.hcl.
  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  name       = "${var.project_name}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  # PyIceberg's Glue catalog uses the database name as the Iceberg namespace.
  glue_database_name = coalesce(var.glue_database_name, replace(local.name, "-", "_"))

  # Connector secrets live under this SSM path (SecureString, default key).
  connector_parameter_path = "/${local.name}/connectors"

  tags = {
    Application = var.project_name
    Environment = var.environment
    Component   = "pipeline"
    ManagedBy   = "Terraform"
    Repository  = var.github_repository
  }
}
