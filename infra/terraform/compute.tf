resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_lb" "app" {
  name               = local.name
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = values(aws_subnet.public)[*].id

  enable_deletion_protection = false
  drop_invalid_header_fields = true

  tags = { Name = local.name }
}

resource "aws_lb_target_group" "app" {
  name        = local.name
  port        = 5000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http_forward" {
  count = var.certificate_arn == null ? 1 : 0

  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  count = var.certificate_arn == null ? 0 : 1

  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  count = var.certificate_arn == null ? 0 : 1

  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.container_cpu)
  memory                   = tostring(var.container_memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "aistora"
      image     = "${aws_ecr_repository.app.repository_url}:bootstrap"
      essential = true

      portMappings = [{
        containerPort = 5000
        hostPort      = 5000
        protocol      = "tcp"
        name          = "http"
      }]

      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "DATASET_STORAGE_BACKEND", value = "s3" },
        { name = "S3_DATASET_BUCKET", value = aws_s3_bucket.datasets.id },
        { name = "S3_DATASET_PREFIX", value = "datasets" },
        { name = "DATASET_CACHE_DIR", value = "/tmp/aistora-cache" },
        { name = "UPLOAD_FOLDER", value = "/tmp/aistora-uploads" },
        { name = "AGENT_AUDIT_PATH", value = "/tmp/aistora-agent-audit.jsonl" },
        { name = "AGENT_CANCELLATION_DIR", value = "/tmp/aistora-cancellations" },
        { name = "APP_ENV", value = "production" },
        # Empty when the cache is disabled; the app then falls back to a
        # per-container session store and warns at startup.
        { name = "REDIS_URL", value = local.session_redis_url },
        { name = "DB_HOST", value = aws_db_instance.main.address },
        { name = "DB_PORT", value = tostring(aws_db_instance.main.port) },
        { name = "DB_NAME", value = aws_db_instance.main.db_name },
        { name = "DB_USER", value = aws_db_instance.main.username },
        { name = "DB_SSLMODE", value = "require" },
        { name = "GEMINI_MODEL", value = var.gemini_model },
        { name = "GEMINI_ADVANCED_MODEL", value = var.gemini_advanced_model },
        { name = "AGENT_MODEL_ROUTING", value = "true" },
        { name = "AGENT_MAX_TURNS", value = "8" },
        { name = "AGENT_MAX_TOOL_CALLS", value = "12" },
        { name = "AGENT_TIMEOUT_SECONDS", value = "45" },
        { name = "AGENT_LLM_MAX_RETRIES", value = "2" },
        { name = "AGENT_HISTORY_EXAMPLES", value = "3" },
        { name = "MAX_UPLOAD_BYTES", value = tostring(50 * 1024 * 1024) },
        { name = "WEB_CONCURRENCY", value = "2" },
        { name = "GUNICORN_THREADS", value = "8" },
        { name = "FLASK_ENV", value = "production" },
      ]

      secrets = [
        {
          name      = "DB_PASSWORD"
          valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::"
        },
        {
          name      = "SECRET_KEY"
          valueFrom = aws_secretsmanager_secret.flask.arn
        },
        {
          name      = "GEMINI_API_KEY"
          valueFrom = aws_secretsmanager_secret.gemini.arn
        },
      ]

      linuxParameters = {
        initProcessEnabled = true
      }

      healthCheck = {
        command = [
          "CMD-SHELL",
          "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3)\" || exit 1",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.app.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "app"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "app" {
  name             = local.name
  cluster          = aws_ecs_cluster.main.id
  task_definition  = aws_ecs_task_definition.app.arn
  desired_count    = var.desired_count
  launch_type      = "FARGATE"
  platform_version = "LATEST"

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  health_check_grace_period_seconds  = 90
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = values(aws_subnet.application)[*].id
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "aistora"
    container_port   = 5000
  }

  lifecycle {
    ignore_changes = [
      desired_count,
      task_definition,
    ]
  }

  depends_on = [
    aws_lb_listener.http_forward,
    aws_lb_listener.http_redirect,
    aws_lb_listener.https,
    aws_iam_role_policy.execution_secrets,
  ]
}
