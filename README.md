# ADC Pipeline — Step 1–6 Database & Storage

Design + reference implementation scaffold for persisting the ADC pipeline's
Step 1–6 outputs. Current phase runs on **Supabase Local / PostgreSQL** and is
built to migrate to **AWS (RDS + S3 + Athena/Glue + optional DynamoDB)** without
structural change.

## Layout

```
docs/                       Human-facing design (read these first)
  00_overview_and_decisions.md   Why Supabase now, AWS later; the 3-tier model; ADRs
  01_data_model.md               Storage structure per step, ID contract, run-scoped keys
  02_read_write_actions.md       Write/read catalog, idempotency contract, artifact lifecycle
  03_aws_migration_roadmap.md    RDS / S3 / Athena+Glue / DynamoDB mapping
  04_ops_and_open_questions.md   Audit, human review, errors, security, open questions
  05_api_and_async_backend.md    REST API (/v1, JWT/RBAC), async workers, outbox, reliability, observability
  06_aws_production_architecture.md  Deployable AWS design (VPC/ECS/SQS/StepFn/IaC/CI-CD) — supersedes 03

supabase/migrations/        SINGLE SOURCE OF TRUTH for the schema
  0001_adc_step_1_6_schema.sql
  0002_backend_async_layer.sql
  0003_align_async_layer.sql
  0004_step_7_9_snapshot_and_v0_3_deltas.sql   # Step 7/8/9 + snapshot + v0.3 field deltas

app/api/                    Service layer (FastAPI) — see docs/05
  main.py  deps.py  schemas.py  v1/runs.py  v1/health.py
app/services/               submit_run() = run + idempotency + outbox in ONE tx
  run_service.py  common.py
app/workers/                Async execution — see docs/05
  queue.py                  local job_queue table | SQS (swap by env)
  outbox_relay.py           publishes outbox -> queue (FOR UPDATE SKIP LOCKED)
  worker.py                 consumes queue -> runs stub step -> step_records
app/db/                     Reference implementation (data layer)
  ids.py                    ID generation contract (ULID + deterministic uuid5)
  session.py                DB engine + object-store client (env-driven)
  storage.py                Object-store client ONLY (put/presign) — no DB
  projection.py             reset_step_projection() — the idempotency helper
  repositories/             One module per step + shared helpers
    steps.py  runs.py  intake.py  structured_query.py  readiness.py
    candidate_context.py  liability.py  artifacts.py
    structure_prep.py  structure_prediction.py  variant_design.py   # Step 7/8/9 (v0.3)

env/                        The ONLY place backend choice lives (copy to real .env)
  local.env.example  localstack.env.example  aws.env.example

tests/
  test_ids.py               unit (no DB): deterministic ids stable, ULIDs unique
  test_idempotency.py       integration: rerun == run-once (projection reset)
  test_artifact_lifecycle.py integration: pending -> available, reaper
  test_schema_roundtrip.py  integration: payload survives JSONB

requirements.txt  .gitignore
```

## Storage modes (set in your `.env`)

| ARTIFACT_STORAGE_BACKEND | Where bytes go | When |
|---|---|---|
| local | local filesystem | default dev, zero extra services |
| supabase_storage | Supabase local Storage (S3 endpoint) | dev with S3-shaped access |
| localstack_s3 | LocalStack S3 | strict S3-parity tests |
| aws_s3 | real Amazon S3 | future AWS |

The DB is plain PostgreSQL in every mode; only the object-store endpoint_url
differs. Migration to AWS = swap the .env, not the code.

## Quickstart

```bash
# 1. bring up Postgres + Storage locally
supabase init && supabase start

# 2. apply the schema (source of truth)
supabase db reset                      # runs supabase/migrations/*.sql

# 3. pick an env and load it
cp env/local.env.example .env          # then edit; real .env is gitignored

# 4. install deps
pip install -r requirements.txt

# 5. tests
pytest tests/test_ids.py               # unit only, no DB needed
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres
pytest                                 # full suite (integration tests need the DB up)

# 6. run the API + async workers (service layer, docs/05)
uvicorn app.api.main:app --reload      # API on :8000  (Swagger at /docs)
python -m app.workers.outbox_relay     # outbox -> job_queue (separate shell)
python -m app.workers.worker           # job_queue -> step_records (separate shell)

# submit a run (dev bearer tokens: dev-submitter / dev-approver / dev-viewer / dev-admin)
curl -XPOST localhost:8000/v1/runs \
  -H 'Authorization: Bearer dev-submitter' -H 'Idempotency-Key: k1' \
  -H 'content-type: application/json' -d '{}'
# re-running with the same Idempotency-Key returns the SAME run (no duplicate)

# optional: strict S3-parity mode
#   docker compose up localstack  &&  cp env/localstack.env.example .env
```

> Integration tests are skipped automatically unless DATABASE_URL is set, so
> `pytest tests/test_ids.py` always works on a bare checkout.

## The one rule that keeps AWS migration free

Portability comes from three disciplines, not the tool choice: (1) plain SQL
migrations, (2) talk to object storage only through the S3 API, (3) keep the
document-of-record (step_records.output_payload JSONB) + relational projection
pattern. Supabase-vs-LocalStack-vs-AWS is then a matter of env/*.env.
