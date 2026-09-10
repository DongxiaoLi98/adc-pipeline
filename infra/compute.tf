locals {
  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  # 非敏感环境变量。DATABASE_URL 与 AUTH_JWT_SECRET 走 Secrets Manager 注入。
  common_env = [
    { name = "ARTIFACT_STORAGE_BACKEND", value = "aws_s3" },
    { name = "S3_BUCKET", value = aws_s3_bucket.artifacts.id },
    { name = "S3_REGION", value = var.region },
    { name = "QUEUE_BACKEND", value = "sqs" },
    { name = "SQS_QUEUE_URL", value = aws_sqs_queue.jobs.url },
  ]

  common_secrets = [
    { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.app.arn}:DATABASE_URL::" },
    { name = "AUTH_JWT_SECRET", valueFrom = "${aws_secretsmanager_secret.app.arn}:AUTH_JWT_SECRET::" },
  ]
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}-${var.env}"
  retention_in_days = var.env == "prod" ? 30 : 7
}

resource "aws_ecs_cluster" "main" {
  name = "${var.name}-${var.env}"
  setting {
    name  = "containerInsights"
    value = var.env == "prod" ? "enabled" : "disabled" # dev 关掉省钱
  }
}

# ---------- 任务定义 ----------
# 同一个镜像，靠 command 区分角色 —— 和 docker-compose 完全一致。

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-${var.env}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.image
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment  = local.common_env
    secrets      = local.common_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name}-${var.env}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  container_definitions = jsonencode([
    {
      name        = "worker"
      image       = local.image
      essential   = true
      command     = ["python", "-m", "app.workers.worker"]
      environment = local.common_env
      secrets     = local.common_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "worker"
        }
      }
    },
    # relay 和 worker 同 task 内跑，省一个 Fargate 任务（约 $9/月）。
    # 队列深度大时应拆开各自伸缩。
    {
      name        = "relay"
      image       = local.image
      essential   = true
      command     = ["python", "-m", "app.workers.outbox_relay"]
      environment = local.common_env
      secrets     = local.common_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "relay"
        }
      }
    },
  ])
}

# 迁移：一次性任务，由 CI 在服务更新前 run-task 触发，不建 service
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${var.name}-${var.env}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn

  container_definitions = jsonencode([{
    name        = "migrate"
    image       = local.image
    essential   = true
    command     = ["python", "scripts/migrate.py"]
    environment = local.common_env
    secrets     = local.common_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  }])
}

# ---------- ALB ----------

resource "aws_lb" "main" {
  name               = "${var.name}-${var.env}"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-${var.env}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/healthz"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }

  deregistration_delay = 30
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# ---------- 服务 ----------

resource "aws_ecs_service" "api" {
  name            = "${var.name}-${var.env}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true # 没有 NAT，靠公网 IP 拉镜像
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]

  lifecycle {
    ignore_changes = [task_definition] # CI 滚动发布后不被 terraform 回滚
  }
}

resource "aws_ecs_service" "worker" {
  name            = "${var.name}-${var.env}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}
