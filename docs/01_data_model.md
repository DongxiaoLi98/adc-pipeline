# 01 — Data Model

> **Source of truth is `supabase/migrations/0001_adc_step_1_6_schema.sql`.**
> This doc explains the shape and the decisions; it does not re-paste the DDL,
> so the two cannot drift.

## Table map (by step)

| Step | Root / summary table | Detail tables |
|---|---|---|
| backbone | `runs`, `step_records` | — |
| registry | `artifacts` | — |
| 1 intake | `raw_requests` | `uploaded_files` |
| 2 structured query | `structured_queries` | `normalized_entities`, `entity_decompositions` → `entity_components` |
| 3 input readiness | `input_readiness` | `missing_input_items`, `uploaded_file_checks` |
| 5 candidate context | `candidate_contexts` | `candidates` → `candidate_identifiers`, `candidate_material_links`; `materials` |
| 6 liability | `liability_summaries` | `candidate_liabilities` → `lane_results` → `liability_flags`; `tool_call_records` |
| ops | `audit_events`, `human_reviews`, `step_errors` | — |

`step_records.output_payload` (JSONB) holds the full step output; volatile
sub-structures (`selection_audit`, `argument_mapping_audit`, `enrichment_selection_audit`,
`parse_warnings`) stay in JSONB rather than being normalized.

## ID contract (`app/db/ids.py`)

Three id kinds, and the whole model depends on keeping them straight:

1. **Global ULID** — cross-cutting rows unique across all runs: `artifacts.artifact_id`
   and child surrogate ids (`normalized_entities`, `liability_flags`, `audit_events`, …).
2. **Deterministic uuid5** — rows that must be idempotent on rerun:
   `step_records.step_record_id = uuid5(run_id, step_id)`, and one-per-run summary
   rows (`structured_queries`, `input_readiness`, `candidate_contexts`,
   `liability_summaries`). Re-running recomputes the same id → upsert in place.
3. **Run-scoped natural ids** — `candidate_id`, `material_id`, `file_id`,
   `tool_call_id` from upstream payloads, unique only within a run.

## Run-scoped keys (ADR-5)

Because `cand_001` / `mat_001` / `file_001` repeat across runs, these tables use
**composite primary keys** and every reference to them uses a **composite foreign
key**:

- PK `(run_id, candidate_id)` → `candidates`
- PK `(run_id, material_id)` → `materials`
- PK `(run_id, file_id)` → `uploaded_files`
- PK `(run_id, candidate_id)` → `candidate_liabilities`
- PK `(run_id, candidate_id, lane_type)` → `lane_results`
- composite FKs from `candidate_identifiers`, `candidate_material_links`,
  `candidate_liabilities`, `lane_results`, `liability_flags`, `human_reviews`.

`artifacts` and `tool_call_records` keep single-column / `(run_id, id)` keys with
globally-unique ULIDs where they are referenced widely, to avoid composite-FK
sprawl.

## `artifacts` — the single pointer table (hardened)

One registry for every file/blob. Bytes live in the object store; only pointers
live here. Columns beyond the obvious ones and why:

- `storage_backend` (`local | supabase_storage | localstack_s3 | aws_s3`),
  `bucket`, `object_key` — programmatic reads and presigned URLs need bucket+key,
  not just a human-readable `storage_uri`.
- `upload_status` (`pending | available | failed`), `etag` — object upload and DB
  write are **not** one transaction; the lifecycle in doc 02 uses these to
  reconcile partial failures. Readers treat only `available` as real.

`uploaded_files.artifact_id` and `materials.value_artifact_id` point here.

## Field-level decisions worth flagging

- `candidate_liabilities` has **no `candidate_summary`** column (not in current
  code); if a summary ever appears it survives in `raw_payload`.
- `missing_input_items.can_continue` is a **plain boolean written by the app**
  (`severity != 'blocking'`), not a generated column — so the severity vocabulary
  can change without a DDL migration.
- `materials` stores **`value_inline`** (small values, e.g. SMILES) or
  **`value_artifact_id`** (large values → object store), exactly one set.
- `uploaded_file_checks.file_exists` (renamed from the reserved word `exists`).

## Upstream alignment notes (verified against the Step 1-6 schema delta)

- **Step 3 evidence** — the current code emits ten flat fields
  (`target_evidence`, `antibody_evidence`, ... `candidate_file_evidence`), not a
  nested `evidence` object. The writer collects them into the `evidence` JSONB
  column (`readiness.py::_collect_evidence`).
- **Step 3 `uploaded_file_checks`** — populated by the readiness writer; the
  upstream `exists` key is mapped to the `file_exists` column.
- **Step 5 `tool_call_records`** — the Step 5 output also carries top-level tool
  calls; these are persisted via the shared `steps.upsert_tool_call` (with
  `candidate_id`/`lane_type` null), and cleared on rerun by `reset_step_projection`.
- **Step 5 `missing_context_flags`** (`list[str]`) — intentionally kept in
  `candidate_contexts.raw_payload` rather than given its own column.
- **`adc_links`** — the five list fields (`target/antibody/payload/linker/dar_material_ids`)
  are projected into `candidate_material_links` (one row per material + role).

## Enum governance (ADR-4)

All enum-like fields are TEXT today. Freeze-then-constrain later, e.g.:

```sql
alter table missing_input_items
  add constraint chk_severity check (severity in ('blocking','warning','optional'));
```
