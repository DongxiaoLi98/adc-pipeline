output "api_url" {
  value       = "http://${aws_lb.main.dns_name}"
  description = "API 入口。生产环境应加 ACM 证书走 HTTPS。"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "sqs_queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "s3_bucket" {
  value = aws_s3_bucket.artifacts.id
}

output "secret_arn" {
  value       = aws_secretsmanager_secret.app.arn
  description = "含 DATABASE_URL 与 AUTH_JWT_SECRET"
}

output "migrate_task_definition" {
  value       = aws_ecs_task_definition.migrate.family
  description = "CI 用 aws ecs run-task 触发迁移"
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}
