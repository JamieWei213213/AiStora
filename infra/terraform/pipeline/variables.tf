variable "aws_region" {
  description = "AWS region for the pipeline stack. Use the same region as the application stack."
  type        = string
  default     = "us-west-2"
}

variable "project_name" {
  description = "Short lowercase name used in resource names."
  type        = string
  default     = "aistora"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.project_name))
    error_message = "project_name must be 2-16 lowercase letters, numbers, or hyphens."
  }
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,10}$", var.environment))
    error_message = "environment must be 2-11 lowercase letters, numbers, or hyphens."
  }
}

variable "github_repository" {
  description = "GitHub repository that owns this stack (tag only)."
  type        = string
  default     = "JamieWei213213/AiStora"
}

variable "lake_force_destroy" {
  description = "Allow Terraform to delete a non-empty lake bucket."
  type        = bool
  default     = false
}

variable "image_tag" {
  description = <<-DESC
    Tag of the pipeline image in the pipeline ECR repository that the Lambda
    function runs. The first apply fails until an image with this tag has been
    pushed: run the "Pipeline" workflow (workflow_dispatch) to build one, then
    `terraform apply -var image_tag=<tag>`. The deploy workflow later updates
    the function code directly, so this value is ignored on subsequent applies.
  DESC
  type        = string
  default     = "bootstrap"
}

variable "lambda_memory_mb" {
  description = "Lambda memory in MiB. 1769 MiB is the size at which a function gets one full vCPU."
  type        = number
  default     = 1769

  validation {
    condition     = var.lambda_memory_mb >= 512 && var.lambda_memory_mb <= 10240
    error_message = "lambda_memory_mb must be between 512 and 10240."
  }
}

variable "lambda_architecture" {
  description = "Lambda instruction set. Must match the platform pipeline/Dockerfile was built for."
  type        = string
  default     = "x86_64"

  validation {
    condition     = contains(["x86_64", "arm64"], var.lambda_architecture)
    error_message = "lambda_architecture must be x86_64 or arm64."
  }
}

variable "lambda_reserved_concurrency" {
  description = "Reserved concurrent executions for the pipeline function; -1 leaves the account pool unreserved."
  type        = number
  default     = -1
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the Lambda and Step Functions log groups."
  type        = number
  default     = 30
}

variable "log_level" {
  description = "Python log level inside the Lambda (LOG_LEVEL)."
  type        = string
  default     = "INFO"
}

variable "max_raw_bytes" {
  description = "Largest raw file the pipeline accepts (PIPELINE_MAX_RAW_BYTES)."
  type        = number
  default     = 524288000
}

variable "glue_database_name" {
  description = "Glue database (Iceberg namespace) for the curated tables. Defaults to the stack name with hyphens replaced by underscores."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.glue_database_name == null || can(regex("^[a-z0-9_]{1,255}$", coalesce(var.glue_database_name, "x")))
    error_message = "glue_database_name must be lowercase letters, digits and underscores."
  }
}

variable "connector_schedules" {
  description = <<-DESC
    Connector runs to schedule, as connector_id => EventBridge Scheduler
    expression, for example { "crm_postgres" = "rate(6 hours)" }. Each entry
    invokes the pipeline Lambda with {"job": "connector", "connector_id": id}.
    The connector's configuration lives at connectors/<id>/config.json in the
    lake and its secret_ref must be an SSM parameter under
    /<project>-<environment>/connectors/.
  DESC
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for id in keys(var.connector_schedules) : can(regex("^[a-z0-9][a-z0-9_-]{0,62}$", id))])
    error_message = "Connector ids are lowercase letters, digits, - and _."
  }
}

variable "alarm_email" {
  description = "Email address subscribed to the pipeline alarm topic. Null disables the subscription."
  type        = string
  default     = null
  nullable    = true
}

variable "github_deploy_role_name" {
  description = <<-DESC
    Name of an existing IAM role assumed by GitHub Actions (the application
    stack creates "<project>-<environment>-github-deploy"). When set, this
    module attaches a policy that lets the role push the pipeline image,
    update the function code and run the smoke test.
  DESC
  type        = string
  default     = null
  nullable    = true
}
