# 容器化与 CI

## 本地一键起全栈

```bash
docker compose up --build
```

起来之后：
* API      http://localhost:8000/docs
* Postgres localhost:54322（宿主机可直连排查）

服务拓扑对应 docs/05 的分层，也对应 AWS 上的部署单元：

| compose 服务 | 作用 | AWS 对应 |
|---|---|---|
| `db` | Postgres | RDS for PostgreSQL |
| `migrate` | 一次性跑迁移，跑完退出 | ECS one-off task（服务更新前执行） |
| `api` | FastAPI | ECS service + ALB |
| `relay` | outbox → job_queue | `QUEUE_BACKEND=sqs` 后可去掉 |
| `worker` | job_queue → step_records | ECS service，按 SQS 队列深度伸缩 |

启动顺序由 `depends_on` 保证：db 健康 → migrate 成功退出 → 三个服务启动。

`api` 和 `worker` 共享 `artifacts` 卷 —— 否则 API 签出的下载 URL 指不到
worker 写的字节。上 S3 之后这个共享卷自然消失。

## 镜像

单镜像多角色：API / relay / worker / migrate 都用同一个镜像，靠 `command`
区分。这样 ECR 里只有一个 tag，ECS task definition 各自覆盖 command 即可。

以非 root（uid 10001）运行。多阶段构建，runtime 层不含编译期依赖。

## 环境变量

`AUTH_JWT_SECRET` 在 compose 里有本地默认值。**任何联网环境必须覆盖**：

```bash
AUTH_JWT_SECRET=$(openssl rand -hex 32) docker compose up
```

不配置时应用会退回开发用明文 token 并打 RuntimeWarning —— 那是给本地用的，
上云前必须换掉（见 app/api/deps.py）。

## CI

`.github/workflows/ci.yml` 三个 job：

* `unit` —— 裸检出跑 `pytest tests/test_ids.py`，不需要数据库
* `integration` —— 起 Postgres service 容器，依次跑：迁移 → 迁移幂等检查
  (`migrate.py --check`) → 全套 pytest → 数据层冒烟（Step 1→9 各写三次验幂等）
  → 端到端（提交 → relay → worker → 断言八步落库）
* `image` —— 前两个通过后构建镜像，并验证镜像内能 import 应用

三个脚本都以退出码表达结果，不依赖 grep 管道 —— GitHub Actions 默认
`bash -eo pipefail`，`cmd | grep -q` 会因为 grep 提前退出触发 SIGPIPE，
让本该通过的步骤失败。

## 运维注意

**迁移文件不是可重入的**（`add constraint` 等 DDL 重复执行会报错），
幂等性由 `schema_migrations` 台账保证。如果台账丢失但库里 schema 是最新的，
用 `python scripts/migrate.py --baseline` 重建台账，不要直接重跑迁移。
