terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.60" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }

  # 首次 apply 前先手工建好 S3 桶与 DynamoDB 锁表，再取消下面的注释并 terraform init -migrate-state
  # backend "s3" {
  #   bucket         = "adc-pipeline-tfstate"
  #   key            = "dev/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "adc-pipeline-tflock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "adc-pipeline"
      Environment = var.env
      ManagedBy   = "terraform"
    }
  }
}
