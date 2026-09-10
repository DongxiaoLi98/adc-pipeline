# CHANGES — v0.3 update

This package updates the Step 1–6 scaffold toward the **v0.2 → v0.3 schema diff**
(`schema_diff_vs_adc_pipeline_io_v0_2`). It is additive and backward-compatible:
existing Step 1–6 code keeps working because every new column is nullable or
defaulted.

## Added

**Migration `supabase/migrations/0004_step_7_9_snapshot_and_v0_3_deltas.sql`**
(validated by a PostgreSQL parser — 15 new tables, 4 ALTERs, 20 indexes):
- **Step 7** `prepared_structure_packages` → `prepared_structure_inputs`,
  `sequence_refs_for_prediction` (sequences stored as length + sha256_prefix + ref).
- **Step 8** `structure_prediction_summaries` → `candidate_structure_results`
  (projects `has_complex_structure` / `has_validated_structure` as indexed booleans;
  full `downstream_handoff` in JSONB), `complex_structure_refs`,
  `interface_analysis_records`, `complex_prediction_plans`.
- **Step 9** `variant_design_summaries` (Stage1/Stage2/runtime audit),
  `step9_input_fields`, `step9_execution_records`.
- **Snapshot** `pipeline_snapshots`, `snapshot_artifact_entries` (refs/hashes/sizes only).
- **v0.3 deltas** to existing tables: `structured_queries.canonical_query` + child
  `query_missing_slots`; child `clarification_requests` (Step 3);
  `materials.content_descriptor`; `lane_results.risk_label` / `assessment_status`;
  `candidate_liabilities.context_completeness` / `assessed_lane_count` / `not_assessed_lane_count`.

**Writers** (follow the standard pattern: write_step_record → reset_step_projection → rebuild):
- `app/db/repositories/structure_prep.py`      (Step 7)
- `app/db/repositories/structure_prediction.py` (Step 8)
- `app/db/repositories/variant_design.py`       (Step 9)

**Idempotency** — `app/db/projection.py::_OWNED` now registers the three new steps,
so Step 7/8/9 reruns are wiped-and-rebuilt exactly like Steps 1–6.

## Design disciplines preserved
- Run-scoped composite PKs for the candidate graph; global ULIDs for child rows.
- Enums stay TEXT (new values are app-layer only; no DDL).
- `on delete cascade` so reset_step_projection wipes a step's subtree via its root.
- Every projection table keeps a `raw_payload` JSONB fallback.
- Large objects (structures, sequences, tool outputs) stored as ref + digest, never raw.

## Intentionally NOT changed (needs a mentor decision first)
These depend on the open questions and on the exact upstream payload shape, so they
are left for a follow-up rather than guessed:

1. **Existing Step 2/3/5/6 writers are not yet populating the new v0.3 columns**
   (e.g. `canonical_query`, `risk_label`, `content_descriptor`, `query_missing_slots`,
   `clarification_requests`). The columns exist and default safely; wiring the writers
   to fill them is a small follow-up once field semantics are confirmed.
2. **`risk_label` vs existing `lane_risk_category`** — kept as two columns for now;
   merge/keep is an open question.
3. **`app/workers/worker.py`** still runs the stub step list. Extending it to call the
   real Step 7/8/9 writers is the "plug into the worker" step (see docs/05).

## Apply
```
supabase db reset          # runs migrations 0001 → 0004 in order
```
