# ElastiCache for Redis: server-side session storage shared by every task.
#
# Disabled by default (enable_elasticache = false) so that `terraform plan`
# on an existing stack shows no change until the cache is deliberately
# adopted. While it is disabled the application falls back to a per-container
# filesystem session store, which is correct for a single task and logs a
# warning at startup when running in production.

locals {
  # rediss:// because transit encryption is enabled on the replication group.
  session_redis_url = var.enable_elasticache ? "rediss://${aws_elasticache_replication_group.sessions[0].primary_endpoint_address}:6379/0" : ""
}

resource "aws_elasticache_subnet_group" "sessions" {
  count = var.enable_elasticache ? 1 : 0

  name       = "${local.name}-sessions"
  subnet_ids = [for subnet in aws_subnet.application : subnet.id]

  tags = { Name = "${local.name}-sessions" }
}

resource "aws_security_group" "cache" {
  count = var.enable_elasticache ? 1 : 0

  name_prefix = "${local.name}-cache-"
  description = "Session cache. Reachable only from the Fargate tasks."
  vpc_id      = aws_vpc.main.id

  lifecycle {
    create_before_destroy = true
  }

  tags = { Name = "${local.name}-cache" }
}

resource "aws_vpc_security_group_ingress_rule" "cache_from_ecs" {
  count = var.enable_elasticache ? 1 : 0

  security_group_id            = aws_security_group.cache[0].id
  description                  = "Only the application tasks may reach the cache"
  referenced_security_group_id = aws_security_group.ecs.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "ecs_to_cache" {
  count = var.enable_elasticache ? 1 : 0

  security_group_id            = aws_security_group.ecs.id
  description                  = "Session cache"
  referenced_security_group_id = aws_security_group.cache[0].id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}

resource "aws_elasticache_replication_group" "sessions" {
  count = var.enable_elasticache ? 1 : 0

  replication_group_id = "${local.name}-sessions"
  description          = "AIStora server-side session storage"

  engine         = "redis"
  engine_version = var.elasticache_engine_version
  node_type      = var.elasticache_node_type
  port           = 6379

  num_cache_clusters         = var.elasticache_multi_az ? 2 : 1
  automatic_failover_enabled = var.elasticache_multi_az
  multi_az_enabled           = var.elasticache_multi_az

  subnet_group_name  = aws_elasticache_subnet_group.sessions[0].name
  security_group_ids = [aws_security_group.cache[0].id]

  # Sessions carry an authenticated user identity, so the data is encrypted
  # both at rest and in transit even though the cache is private to the VPC.
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  apply_immediately        = true
  snapshot_retention_limit = 0

  tags = { Name = "${local.name}-sessions" }
}
