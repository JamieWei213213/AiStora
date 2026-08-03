resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = values(aws_subnet.database)[*].id

  tags = { Name = "${local.name}-database" }
}

resource "aws_db_parameter_group" "main" {
  name   = local.name
  family = "postgres16"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  tags = { Name = "${local.name}-postgres16" }
}

resource "aws_db_instance" "main" {
  identifier = local.name

  engine         = "postgres"
  engine_version = "16"
  instance_class = var.database_instance_class

  db_name  = "aistora"
  username = "aistora_admin"
  port     = 5432

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  parameter_group_name   = aws_db_parameter_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = var.database_multi_az

  backup_retention_period    = var.database_backup_retention_days
  backup_window              = "09:00-10:00"
  maintenance_window         = "sun:10:00-sun:11:00"
  auto_minor_version_upgrade = true

  deletion_protection = var.database_deletion_protection
  skip_final_snapshot = var.database_skip_final_snapshot

  copy_tags_to_snapshot = true

  tags = { Name = "${local.name}-postgres" }
}

resource "random_password" "flask_secret" {
  length  = 64
  special = true
}

resource "aws_secretsmanager_secret" "flask" {
  name                    = "${local.name}/flask-secret-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "flask" {
  secret_id     = aws_secretsmanager_secret.flask.id
  secret_string = random_password.flask_secret.result
}

resource "aws_secretsmanager_secret" "gemini" {
  name                    = "${local.name}/gemini-api-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "gemini" {
  secret_id     = aws_secretsmanager_secret.gemini.id
  secret_string = "configure-in-secrets-manager-before-the-first-deployment"

  lifecycle {
    ignore_changes = [secret_string]
  }
}
