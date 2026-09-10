# ADC Pipeline — Step 1–6 Database & Storage Design
## Review Guide

Prepared for review. This package is the database/storage design for persisting
the ADC pipeline's Step 1–6 outputs, plus a reference implementation scaffold.
It is a **design + skeleton for review**, not a finished production service.

---

### 1. What this is (30-second version)

Following our discussion, AWS-managed services stay on the **future cloud
migration roadmap**. For the current phase we build on **Supabase Local /
PostgreSQL** with an S3-compatible object store, designed so that migrating to
**AWS (RDS + S3 + Athena/Glue + optional DynamoDB)** later is a configuration
change, not a redesign.

Core design in one line: the full output of each pipeline step is stored
verbatim as a JSONB "document of record"; stable fields are projected into typed
relational tables for querying; large files live in the object store with only
pointers in Postgres; and every write is idempotent (re-running a step produces
the same result, never duplicates).

---

### 2. What to read, and in what order (~15 min)

All design docs are in `docs/`, meant to be read in order:

1. **`docs/00_overview_and_decisions.md`** — the three-tier model and the six
   architecture decisions (ADRs). **If you read only one file, read this.**
2. `docs/01_data_model.md` — how each step's data is stored; identifier strategy.
3. `docs/02_read_write_actions.md` — how data is written/read; the idempotency
   guarantee; how file uploads are handled.
4. `docs/03_aws_migration_roadmap.md` — the AWS mapping and cutover steps.
5. `docs/04_ops_and_open_questions.md` — governance, security, and the open
   questions where your input would help.

The database schema itself (the single source of truth) is
`supabase/migrations/0001_adc_step_1_6_schema.sql` (25 tables). The reference
code is under `app/db/`, and tests under `tests/`.

---

### 3. Decisions I'd like your sign-off on

These are the load-bearing choices (full rationale in `docs/00`):

| # | Decision | Why |
|---|---|---|
| 1 | **Supabase Local / PostgreSQL** for the current phase | Postgres + S3-compatible storage + SQL migrations, free locally, maps 1:1 to RDS + S3 |
| 2 | **Do not use MinIO** | Its open-source edition is no longer maintained and images are no longer published; the supported product is enterprise-priced. We use Supabase Storage locally, LocalStack for strict S3 tests |
| 3 | **JSONB document-of-record + relational projection** | Tolerates the still-drifting upstream schema without losing data |
| 4 | **Enums stored as TEXT for now** | Enum value sets are still changing; native Postgres enums are painful to alter |
| 5 | **Run-scoped identifiers** (composite keys) | `candidate_id` etc. are only unique within a run |
| 6 | **Idempotent writes** | Re-running a step must not duplicate rows |

---

### 4. How to run it (optional, ~5 min)

Not required for review, but if you'd like to try it:

```bash
supabase init && supabase start          # local Postgres + Storage
supabase db reset                        # applies the schema
pip install -r requirements.txt
pytest tests/test_ids.py                 # unit tests, no database needed
# full suite (needs the DB up):
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres
pytest
```

---

### 5. Open questions where your input would help

(Full list in `docs/04`.) The main ones:

1. **Retention** — how long should full step outputs and artifacts be kept before
   archiving to cheaper storage?
2. **Access control / PII** — the intake record carries a submitter identity; how
   strict should user/project-level access be in this phase?
3. **When to migrate to AWS** — is there a target trigger (data volume, user
   count, funding) for standing up the AWS account?

---

### 6. Scope and honest status

**In scope / done:** Steps 1, 2, 3, 5, 6 — schema, read/write logic, idempotency,
artifact handling, AWS migration mapping, and the design docs above. Unit tests
pass; integration tests run against a local Postgres.

**Now added (backend milestone, docs/05):** a REST API layer (FastAPI, `/v1`,
stub auth + RBAC), a transactional-outbox + relay + worker async path with a
local `job_queue` that swaps to SQS by env, API-level idempotency, and the
hardened async schema (`0003`). The pipeline step body is still a stub — the
point of this milestone is the production-shaped control plane (idempotency,
outbox/DLQ, SKIP-LOCKED claiming, run state + audit), not the Step 1–6 science.

**Still not included (intentionally):** Steps 4/7/13/14 and their `scores` /
`rankings` / `ip_record` tables; real JWT issuance (dev bearer tokens stand in);
a live AWS deployment (design is complete in docs/06 — RDS/S3/ECS/SQS); no ML
model step (this milestone is backend + AWS only).

I'm happy to walk through any part of this or adjust the direction based on your
feedback.
