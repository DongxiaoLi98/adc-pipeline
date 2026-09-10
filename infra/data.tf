# ---------- RDS ----------

resource "random_password" "db" {
  length  = 32
  special = false # 避免 URL 里需要转义
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.name}-${var.env}"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier     = "${var.name}-${var.env}"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = 100 # 存储自动伸缩上限
  storage_encrypted     = true
  storage_type          = "gp3"

  db_name  = "adc"
  username = "adcadmin"
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false

  multi_az                = var.env == "prod"
  backup_retention_period = var.env == "prod" ? 7 : 1
  skip_final_snapshot     = var.env != "prod"
  deletion_protection     = var.env == "prod"

  performance_insights_enabled = false # dev 省钱
  apply_immediately            = var.env != "prod"
}

# ---------- S3 ----------

resource "aws_s3_bucket" "artifacts" {
  bucket        = "${var.name}-${var.env}-artifacts-${data.aws_caller_identity.current.account_id}"
  force_destroy = var.env != "prod"
}

data "aws_caller_identity" "current" {}

# 版本控制 —— 补上 doc 02 指出的 "Supabase Storage 没有版本控制" 的缺口
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 生命周期：热 -> IA -> Glacier。阈值待 doc 04 的保留策略确认后调整
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "tiering"
    status = "Enabled"
    filter {}

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
}

# ---------- SQS ----------

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name}-${var.env}-dlq"
  message_retention_seconds = 1209600 # 14 天
}

resource "aws_sqs_queue" "jobs" {
  name = "${var.name}-${var.env}-jobs"

  # 可见性超时必须大于最长一步的耗时，否则任务会被重复投递
  visibility_timeout_seconds = 300
  message_retention_seconds  = 345600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 5
  })
}

# ---------- Secrets ----------

resource "random_password" "jwt" {
  length  = 48
  special = false
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/${var.env}/app"
  recovery_window_in_days = 0 # dev 可立即删除重建
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL    = "postgresql+psycopg://${aws_db_instance.main.username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${aws_db_instance.main.db_name}"
    AUTH_JWT_SECRET = random_password.jwt.result
  })
}

# ---------- ECR ----------

resource "aws_ecr_repository" "app" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"
  force_delete         = var.env != "prod"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "只保留最近 10 个镜像"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}
