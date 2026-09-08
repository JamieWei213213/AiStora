variable "aws_region" {
  description = "AWS region for the complete AIStora stack."
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
  description = "GitHub repository allowed to assume the deployment role."
  type        = string
  default     = "JamieWei213213/AiStora"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must use the owner/repository format."
  }
}

variable "github_branch" {
  description = "Only this branch can assume the GitHub Actions deployment role."
  type        = string
  default     = "main"
}

variable "github_oidc_provider_arn" {
  description = "ARN of an existing GitHub OIDC provider in this account, if one already exists."
  type        = string
  default     = null
  nullable    = true
}

variable "vpc_cidr" {
  description = "Private network range for AIStora."
  type        = string
  default     = "10.42.0.0/16"
}

variable "container_cpu" {
  description = "Fargate CPU units."
  type        = number
  default     = 512
}

variable "container_memory" {
  description = "Fargate memory in MiB."
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Initial task count. Keep zero until the first image is pushed."
  type        = number
  default     = 0
}

variable "database_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "database_multi_az" {
  description = "Run RDS with a synchronous standby in another AZ."
  type        = bool
  default     = false
}

variable "database_backup_retention_days" {
  description = "Number of days RDS retains automated backups. New AWS free-tier accounts may require 1."
  type        = number
  default     = 7

  validation {
    condition     = var.database_backup_retention_days >= 0 && var.database_backup_retention_days <= 35
    error_message = "database_backup_retention_days must be between 0 and 35."
  }
}

variable "database_deletion_protection" {
  description = "Protect the RDS instance from deletion."
  type        = bool
  default     = false
}

variable "database_skip_final_snapshot" {
  description = "Skip a final RDS snapshot when the development stack is destroyed."
  type        = bool
  default     = true
}

variable "dataset_force_destroy" {
  description = "Allow Terraform to delete a non-empty dataset bucket."
  type        = bool
  default     = false
}

variable "certificate_arn" {
  description = "Optional ACM certificate ARN. When set, HTTP redirects to HTTPS."
  type        = string
  default     = null
  nullable    = true
}

variable "gemini_model" {
  description = "Standard Gemini model used by the agent."
  type        = string
  default     = "gemini-3.1-flash-lite"
}

variable "gemini_advanced_model" {
  description = "Model used for complex routed requests."
  type        = string
  default     = "gemini-3.6-flash"
}

variable "enable_elasticache" {
  description = <<-DESC
    Provision ElastiCache for Redis to hold server-side sessions.

    Defaults to false so that adopting this module is an explicit decision.
    With a single Fargate task the filesystem fallback works; with more than
    one, sessions must be shared or users are logged out as requests move
    between tasks.
  DESC
  type        = bool
  default     = false
}

variable "elasticache_node_type" {
  description = "ElastiCache node size. cache.t4g.micro is the cheapest option."
  type        = string
  default     = "cache.t4g.micro"
}

variable "elasticache_engine_version" {
  description = "Redis engine version for the session cache."
  type        = string
  default     = "7.1"
}

variable "elasticache_multi_az" {
  description = "Run a replica in a second AZ with automatic failover."
  type        = bool
  default     = false
}
