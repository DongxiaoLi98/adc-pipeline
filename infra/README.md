# AWS 部署（A + B 阶段）

对应 docs/06 的相位 A（数据面：RDS + S3）和 B（服务面：ECR + ECS + ALB + SQS）。
相位 C（Step Functions）、D（Athena/Glue）、E（多 AZ/告警）暂未包含。

## 与 docs/06 的偏离：不建 NAT 网关

doc 06 写的是 ECS 任务放私有子网 + NAT。NAT 网关每个约 **$32/月**，对 dev
环境不划算。这里的做法是任务放公有子网 + 公网 IP（拉 ECR 镜像、写 CloudWatch
日志需要出站路径），入站由安全组完全封死：只有 ALB 能连任务的 8000 端口，
只有任务安全组能连 RDS 的 5432。RDS `publicly_accessible = false`。

S3 走网关端点（免费），流量不出 VPC。

**生产环境应改回私有子网 + NAT，或加 ECR/Logs/Secrets 的接口端点**
（接口端点约 $7.2/月/个，四个就和一个 NAT 差不多，按需权衡）。

## 成本（us-east-1，dev 规格，粗估）

| 项 | 月成本 |
|---|---|
| ALB | ~$16（固定，无流量也收） |
| RDS db.t4g.micro 单 AZ 20GB gp3 | ~$13 |
| Fargate api（0.25 vCPU / 0.5GB，常驻） | ~$9 |
| Fargate worker（同上，含 relay 边车） | ~$9 |
| S3 / SQS / ECR / CloudWatch | 几美元 |
| **合计** | **约 $50/月** |

relay 作为边车跑在 worker 任务里，省掉一个常驻任务。队列压力大时应拆开各自伸缩。

**不用时销毁**：`terraform destroy`。dev 环境 `skip_final_snapshot = true`、
`force_destroy = true`，可以干净删掉重建。

## 前置

```bash
aws configure          # 或用 SSO
terraform -v           # >= 1.6
```

## 首次部署

```bash
cd infra
terraform init
terraform fmt -check    # 我无法在本地验证 HCL，请务必先跑这两步
terraform validate
terraform plan          # 仔细看清单，确认没有意料之外的资源
terraform apply
```

apply 后拿到 ECR 地址，推第一个镜像（任务定义默认引用 `latest`）：

```bash
ECR=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${ECR%%/*}"
docker build -t "$ECR:latest" ..
docker push "$ECR:latest"
```

然后跑迁移（一次性任务），再确认服务健康：

```bash
terraform output -raw api_url
curl "$(terraform output -raw api_url)/readyz"
```

## 后续发布

`.github/workflows/deploy.yml`，手动触发。流程：构建推镜像 → 为三个 family
注册指向新镜像的任务定义修订版 → 跑迁移一次性任务并等它退出码为 0 →
滚动更新 api/worker → 打 `/healthz` `/readyz` 冒烟。

**注意**：仅 `force-new-deployment` 是不够的，那只会重拉任务定义里写死的旧 tag。
必须注册新修订版。

需要在仓库 Secrets 里配 `AWS_DEPLOY_ROLE_ARN`，并在 AWS 侧建好信任 GitHub OIDC
的角色（不要用长期 access key）。该角色需要 ECR 推送、ECS 注册任务定义/更新服务/
运行任务、以及 iam:PassRole 给两个任务角色的权限。

## 未验证事项

infra/ 下的 HCL **没有经过 terraform validate 或 plan**（编写环境无法安装
terraform）。首次 apply 前请务必自己跑 fmt/validate/plan。已知易错点已自查：
变量类型、ALB/目标组名长度、Secrets Manager 的 JSON 键提取语法
（`arn:KEY::`）、S3 桶名全局唯一性（拼了账号 ID）。

## 代码侧无需改动

应用代码一行没动。切换靠环境变量：
`ARTIFACT_STORAGE_BACKEND=aws_s3` + 不设 `S3_ENDPOINT_URL` → boto3 走真实 S3；
`QUEUE_BACKEND=sqs` + `SQS_QUEUE_URL` → app/workers/queue.py 换到 SQS。
`DATABASE_URL` 与 `AUTH_JWT_SECRET` 由 Secrets Manager 注入。
这正是 docs/00 那三条可移植性纪律的兑现。
