# 00 — Overview & Decisions

## Context

We are persisting the ADC pipeline's Step 1–6 outputs. The upstream schema (see
the Step 1–6 schema delta) is still drifting from the v0.1 IO spec: step ids
renamed, enum value sets changing, some objects collapsed to `list[dict]`, new
audit structures added. The storage design must therefore tolerate ongoing
schema change and must not lock us into AWS before an account exists.

## The three-tier model

| Tier | Holds | Now (dev) | Later (AWS) |
|---|---|---|---|
| Relational | run/step backbone, candidates, materials, liabilities, tool calls, artifact pointers | Supabase Local / PostgreSQL | RDS for PostgreSQL |
| Object store | uploaded files, tool outputs, reports, Parquet exports | Supabase Storage (file-backed, S3-compatible) / LocalStack | Amazon S3 |
| Analytics | historical exports | DuckDB over Parquet | Athena + Glue |

The full step output is always kept verbatim as JSONB in `step_records.output_payload`
(the document of record); only stable, cross-run-queryable fields are projected
into typed tables.

## Decision records

**ADR-1 — Supabase Local / PostgreSQL is the current-phase database.**
It gives Postgres + an S3-compatible Storage endpoint + Studio + SQL migrations,
free locally, and maps 1:1 onto RDS + S3. Migrations are plain SQL, portable to
RDS unchanged.

**ADR-2 — Do not depend on MinIO.** MinIO's open-source repository is no longer
maintained and open-source Docker images are no longer published; the supported
product is enterprise-priced. So it is not the low-cost local option it used to
be. For local object storage we use Supabase Storage; for strict S3-parity tests
we use LocalStack S3 or a small real S3 bucket. (This directly answers the
“is MinIO free?” question: the old free edition is effectively discontinued.)

**ADR-3 — Document-of-record + relational projection.** Because the schema keeps
drifting, we never lose information: the canonical output is JSONB, and
projections are derived and rebuildable. This also keeps a future DynamoDB read
model free (a `step_records` row maps directly to a Dynamo item, PK=run_id,
SK=step_id).

**ADR-4 — Enums as TEXT for now.** The delta shows enum value sets still changing
(`intake_status`, lane `run_status`, `lane_risk_category`, …). Native Postgres
ENUM types are painful to `ALTER`, so we use TEXT + app-layer (Pydantic)
validation, and add CHECK constraints only once a value set is frozen.

**ADR-5 — Run-scoped identifiers.** `candidate_id`, `material_id`, `file_id`,
`tool_call_id` come from upstream payloads and are only unique within a run, so
they are stored under composite keys `(run_id, <id>)`. Cross-cutting registries
(`artifacts`) use globally-unique ULIDs. See doc 01.

**ADR-6 — Idempotent writes.** Every step write is “rerun == run-once”: the
step record upserts on `(run_id, step_id)`, and child projections are wiped by
`reset_step_projection()` (cascade) then rebuilt. See doc 02.

## Scope

Steps 1, 2, 3, 5, 6. Steps 4/7/13/14 are out of scope for this milestone;
`scores` / `rankings` / `ip_record` are noted as forward-looking tables in doc 03.
