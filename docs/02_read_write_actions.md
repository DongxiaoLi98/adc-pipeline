# 02 — Read / Write Actions

> Implementation: `app/db/repositories/*.py`. This doc is the contract.

## Write actions

| Action | Repository fn | Tables written (after `reset_step_projection`) |
|---|---|---|
| create run | `runs.create_run` | `runs` |
| Step 1 | `intake.write_intake` | `step_records`, `raw_requests`, `uploaded_files`, `artifacts` |
| Step 2 | `structured_query.write_structured_query` | `step_records`, `structured_queries`, `normalized_entities`, `entity_decompositions`, `entity_components` |
| Step 3 | `readiness.write_readiness` | `step_records`, `input_readiness`, `missing_input_items`, `uploaded_file_checks` |
| Step 5 | `candidate_context.write_candidate_context` | `step_records`, `candidate_contexts`, `candidates`, `candidate_identifiers`, `materials`, `candidate_material_links` |
| Step 6 | `liability.write_liability_summary` | `step_records`, `liability_summaries`, `candidate_liabilities`, `lane_results`, `liability_flags`, `tool_call_records` |

## Read actions

| Action | Repository fn | Main tables |
|---|---|---|
| run status | `runs.get_run_status` | `runs`, `step_records` |
| step output (full) | `steps.get_step_output` | `step_records` |
| Step 2 for Step 3 | `structured_query.read_for_readiness` | `structured_queries`, `normalized_entities`, `uploaded_files` |
| missing inputs | `readiness.get_missing_inputs` | `input_readiness`, `missing_input_items` |
| liability report | `liability.get_liability_report` | `candidate_liabilities`, `lane_results`, `liability_flags` |
| artifact URL | `artifacts.get_available_url` | `artifacts` |

## Idempotency contract (ADR-6)

Every step write runs in one transaction and is **rerun-safe**:

1. `write_step_record()` upserts on `(run_id, step_id)` (deterministic id).
2. `reset_step_projection(run_id, step_id)` deletes the step's **owned root rows**
   for that run; `on delete cascade` wipes the detail rows beneath.
3. The writer re-inserts a fresh set.

So running a step N times yields the same rows as running it once. The
regression test is `tests/test_idempotency.py`. Ownership → cascade map:

| step | root delete(s) (by run_id) | cascades to |
|---|---|---|
| 1 | `raw_requests`; `artifacts (type=uploaded_file)` | `uploaded_files` → `uploaded_file_checks` |
| 2 | `structured_queries` | `normalized_entities`, `entity_decompositions` → `entity_components` |
| 3 | `input_readiness` | `missing_input_items`, `uploaded_file_checks` |
| 5 | `candidate_contexts`; `materials` | `candidates` → `identifiers`, `links` |
| 6 | `liability_summaries`; `tool_call_records (step_06)` | `candidate_liabilities` → `lane_results` → `liability_flags` |

`tool_call_records` is treated as the **current-step projection** here (rebuilt on
same-run rerun). Immutable audit history, when needed, is written separately to
`audit_events` — see doc 04.

## Artifact upload lifecycle (`app/db/repositories/artifacts.py`)

Object upload and the DB write are not one transaction, so we sequence them and
reconcile with `upload_status`:

1. `store_artifact()` inserts an `artifacts` row as **`pending`**.
2. It PUTs the bytes to the object store (skipped for the `local` backend demo).
3. It flips the row to **`available`** and records `etag`.

If the process dies between (1) and (3), the row stays `pending`; `reap_orphans()`
(scheduled) deletes rows stuck past a timeout. **Readers must treat only
`available` artifacts as real** (`presigned_url()` returns `None` otherwise).

## Note on Supabase Storage vs full S3

Supabase Storage gives an S3-compatible development interface (it exposes an
S3 protocol endpoint and works with the default file backend), but it is **not**
a complete replacement for every AWS S3 feature — notably it does not implement
S3 object versioning. For strict S3-parity tests we use LocalStack S3 or a real
S3 bucket.
