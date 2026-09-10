variable "region" {
  type    = string
  default = "us-east-1"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "name" {
  type    = string
  default = "adc-pipeline"
}

variable "db_instance_class" {
  description = "dev 用最小规格；prod 需上调并开 Multi-AZ"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "task_cpu" {
  type    = number
  default = 256 # 0.25 vCPU
}

variable "task_memory" {
  type    = number
  default = 512
}

variable "image_tag" {
  description = "ECR 镜像 tag；CI 里传 git sha"
  type        = string
  default     = "latest"
}

variable "allowed_ingress_cidr" {
  description = "允许访问 ALB 的来源。默认全网开放，建议改成你的出口 IP/32"
  type        = string
  default     = "0.0.0.0/0"
}
