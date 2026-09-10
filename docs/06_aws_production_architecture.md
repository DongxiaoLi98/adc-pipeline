# 06 — AWS Production Architecture

Doc 03 was a **migration mapping** (which managed service replaces which local
one). This doc upgrades it to a **deployable production architecture**: the
compute, networking, orchestration, security, observability, IaC and CI/CD that
doc 03 left implicit. It supersedes doc 03's "cutover steps" while keeping its
core promise — *the application code does not change; only env + infra does.*

---

## 1. Target architecture

```
                    ┌─────────────── AWS account / region ───────────────┐
   Internet ──▶ ALB ─▶ ECS Fargate: API (FastAPI /v1)   [private subnets]
                        │   │ writes runs+outbox (RDS, one tx)
                        │   └─ presigned PUT/GET ──▶ S3 (KMS, versioned)
                        ▼
                 SQS (run.submitted)  ──poison──▶ SQS DLQ
                        │
                        ▼
        Step Functions (Step 1→6, retry/catch per state)
                        │  invokes
                        ▼
             ECS Fargate: worker tasks  ──▶ RDS (Multi-AZ, private)
                        │                └─▶ S3 artifacts
                        └─ every action ──▶ audit_events (RDS)

   EventBridge Scheduler ──▶ Lambda: reaper (reap_orphans)   [cron]
   Glue crawler ──▶ Athena  over  s3://adc-analytics/ (partitioned Parquet)

   Observability: OTel Collector ─▶ CloudWatch (logs/metrics/alarms) + X-Ray
   Secrets Manager (DB/S3 creds) · KMS (S3+RDS) · CloudTrail (audit) · IAM (least-priv)
   Everything provisioned by Terraform; deployed by GitHub Actions → ECR → ECS.
```

---

## 2. Component decisions

| Concern | Service | Notes |
|---|---|---|
| Primary DB | **RDS for PostgreSQL**, Multi-AZ | replay `migrations/*.sql` unchanged (ADR-1); private subnets only |
| Object store | **S3** — **versioning on**, **KMS-encrypted**, lifecycle rules | fixes doc 02's "Supabase Storage has no versioning" gap |
| Job delivery | **SQS** standard + **DLQ** | `outbox` relay publishes here; visibility timeout > longest step |
| Pipeline orchestration | **Step Functions** | one state per Step 1→6; `Retry`/`Catch` per state; worker = task |
| API compute | **ECS Fargate** behind **ALB** | (Alt: API Gateway + Lambda if traffic is spiky/low) |
| Worker compute | **ECS Fargate** service, autoscaled on queue depth | scale to zero off-peak for cost |
| Reaper | **EventBridge Scheduler → Lambda** | answers doc 04 open-Q #4 (reaper cadence) |
| Analytics | **Athena + Glue** over partitioned Parquet | SQL identical to local DuckDB (doc 03) |
| Fast status reads (optional) | **DynamoDB** | mirror `runs` + latest `step_records`; ADR-3 keeps this free |

## 3. Networking & security

- **VPC** with public subnets (ALB + NAT) and **private subnets** (ECS tasks,
  RDS). RDS is never publicly reachable.
- **VPC Gateway Endpoint for S3** (and interface endpoints for SQS/Secrets) so
  worker→S3/SQS traffic stays off the public internet and avoids NAT cost.
- **IAM least-privilege, one role per service**: the API role can write
  `runs`+`outbox` and presign to one bucket prefix; the worker role can read the
  queue and write step prefixes; the reaper role can only list/delete orphaned
  objects. No shared "god" role.
- **Secrets Manager** for DB and S3 creds (rotation enabled); tasks read them at
  runtime — never baked into images or env files.
- **KMS** CMKs encrypt S3 objects and the RDS volume; artifact downloads are
  **presigned URLs with short TTL**, never public.
- **CloudTrail** on for API-call audit; application audit stays in
  `audit_events` (doc 04/05). PII rule from doc 04 holds: `submitted_by` never
  in object keys or logs.
- **RLS → IAM/S3 policy** mapping: the Supabase RLS draft (doc 04) becomes
  IAM + row-scoping in the API's authZ layer.

## 4. Observability

- **OTel Collector** sidecar/service exports traces to **X-Ray** and
  metrics/logs to **CloudWatch**. `run_id` is the trace correlation id (doc 05).
- **CloudWatch alarms** on: run failure rate, per-step p95 latency, SQS DLQ
  depth > 0, `outbox` unpublished-age (relay lag), reaper reclaim spikes, RDS
  CPU/connections. Alarms page via SNS.
- **Dashboards**: one operational (queue depth, latency, error rate, cost) and
  one per-run drill-down (steps, artifacts, audit trail).

## 5. IaC & CI/CD

- **Terraform** modules: `network`, `data` (RDS+S3+KMS), `queue` (SQS+DLQ),
  `compute` (ECS/ALB/autoscale), `orchestration` (Step Functions +
  EventBridge+Lambda reaper), `observability`. Remote state in S3 + DynamoDB
  lock; `dev`/`staging`/`prod` workspaces.
- **GitHub Actions**: `lint → pytest (unit) → integration (ephemeral Postgres) →
  build image → push ECR → terraform plan/apply → ECS rolling deploy`. Migrations
  run as a one-off ECS task before the service update; rollback = redeploy prior
  task-def + `alembic downgrade` if needed.

## 6. Phased cutover (keeps the "config change, not redesign" promise)

| Phase | Stand up | Result |
|---|---|---|
| **A · Data plane** | RDS (replay migrations) + S3 (versioning/KMS/lifecycle) | point `DATABASE_URL`+`aws.env` at AWS; app code unchanged |
| **B · Service plane** | ECR + ECS API/worker + ALB + SQS/DLQ + Secrets/IAM | the doc-05 API/worker running on AWS |
| **C · Orchestration** | Step Functions (Step 1→6) + EventBridge reaper | pipeline ret/catch + scheduled reaper |
| **D · Analytics** | Glue crawler + Athena over Parquet prefix | historical queries, same SQL as DuckDB |
| **E · Hardening** | Multi-AZ, autoscaling, alarms, dashboards, cost budgets | production-ready |

Each phase is independently shippable; A+B alone is already a deployed backend
you can demo and interview on.

## 7. Cost-conscious defaults (JDs ask for this explicitly)

- Fargate workers **autoscale on SQS depth, scale to zero** off-peak.
- **S3 lifecycle**: hot → Infrequent Access → Glacier per the retention policy
  (doc 04 open-Q #5); Parquet + partition-by-date/step keeps Athena scans cheap.
- **S3/SQS VPC endpoints** cut NAT data-processing cost.
- **RDS**: start single-AZ in dev, Multi-AZ only in prod; right-size then enable
  storage autoscaling.
- **AWS Budgets + Cost Explorer** alarm; nothing GPU by default.

## 8. Open items to confirm (carry over from doc 04)

- Retention thresholds → drive the S3 lifecycle rule (§7).
- Enum freeze (ADR-4) → add CHECK constraints before locking the API contract.
- Whether DynamoDB read-model is worth it (only if the UI needs sub-100ms status
  reads at scale) — otherwise RDS + a small cache covers it.

Reading order: docs 00→02 (data) → 05 (API/async) → **06 (this)**.
Supersedes doc 03 for anything beyond the raw service-mapping table.
