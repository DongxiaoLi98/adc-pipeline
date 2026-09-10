-- =====================================================================
-- ADC Pipeline — Migration 0004
-- Supplements 0001 (Steps 1-6) with:
--   * Step 7  prepared_structure_input_package
--   * Step 8  structure_prediction_and_interface_results
--   * Step 9  structure_variant_and_design_results (Stage1/Stage2/runtime)
--   * Shared  pipeline snapshot / hydrate manifest storage
--   * v0.3 field deltas to existing Step 2/3/5/6 projection tables
--
-- Design conventions inherited from 0001 (see docs/01_data_model.md):
--   * Run-scoped composite PKs for the candidate graph; composite FKs to it.
--   * Global ULID single-column keys for cross-cutting child detail rows.
--   * Deterministic uuid5 (app layer) for one-per-run summary rows.
--   * Enums stay TEXT (values still drifting per the v0.2->v0.3 diff);
--     CHECK constraints added only once a value set is frozen.
--   * on delete cascade so reset_step_projection() can wipe a step's
--     owned rows by deleting the root row.
--   * Every projection table keeps a raw_payload JSONB fallback so no
--     upstream field is ever lost (document-of-record + projection).
--   * NEVER store raw PDB/CIF/FASTA/protein sequence/A3M/tool payloads/
--     API keys/full prompts/raw LLM responses here. Use length + sha256
--     prefix + storage ref (into artifacts) instead.
-- =====================================================================


-- ---------- v0.3 deltas to existing tables (cheap ALTERs) ----------
-- Step 1: previous_* clarification-revision fields live in
--   raw_requests.raw_payload (JSONB) — no DDL needed. (documented only)

-- Step 2: canonical_query projected for audit/search (never replaces raw_user_query).
alter table structured_queries add column if not exists canonical_query text;

-- Step 2: machine-readable missing slots. Mirrors missing_input_items; its own
-- child table because slot_name/severity are index candidates for "blocked runs".
create table if not exists query_missing_slots (
  query_missing_slot_id text primary key,                         -- ULID
  run_id                text not null references runs(run_id) on delete cascade,
  structured_query_id   text not null references structured_queries(structured_query_id) on delete cascade,
  slot_name             text not null,                            -- target_or_antigen | antibody | payload | linker | sequence_role | prompt_sequence | other
  slot_category         text,
  severity              text not null,                            -- blocking | warning | optional (TEXT for now)
  required_for          text[] not null default '{}',
  reason                text,
  suggested_question    text,
  evidence              jsonb not null default '{}'::jsonb,
  raw_payload           jsonb not null default '{}'::jsonb,
  created_at            timestamptz not null default now()
);
create index if not exists idx_query_missing_slots_run  on query_missing_slots (run_id, severity);
create index if not exists idx_query_missing_slots_slot on query_missing_slots (run_id, slot_name);

-- Step 3: machine-readable clarification questions (derived from Step 2 missing_slots).
create table if not exists clarification_requests (
  clarification_request_id text primary key,                      -- ULID
  run_id                   text not null references runs(run_id) on delete cascade,
  input_readiness_id       text not null references input_readiness(input_readiness_id) on delete cascade,
  request_id               text not null,                         -- stable business id (from payload)
  slot_name                text not null,
  slot_category            text,
  severity                 text not null,                         -- blocking | warning | optional
  question                 text not null,
  reason                   text,
  source                   text,                                  -- usually 'step_02_missing_slots'
  evidence_field           text,
  resolved                 boolean not null default false,
  raw_payload              jsonb not null default '{}'::jsonb,
  created_at               timestamptz not null default now()
);
create index if not exists idx_clarification_requests_run on clarification_requests (run_id, severity);
create index if not exists idx_clarification_requests_unresolved
  on clarification_requests (run_id) where resolved = false;

-- Step 5: compact material content descriptor (no raw payload inside).
alter table materials add column if not exists content_descriptor jsonb;
-- Note: heavy/light chain semantics are new material_type TEXT values
--   (antibody_heavy_chain_sequence / antibody_light_chain_sequence) — no DDL.

-- Step 6: lane-level and candidate-level interpretation fields.
alter table lane_results
  add column if not exists assessment_status text,               -- assessed | no_signal | signal_detected | not_assessed_* | partial_upstream_error | failed
  add column if not exists risk_label        text;               -- low | review | high | unknown | not_assessed  (index candidate)
create index if not exists idx_lane_results_risk_label on lane_results (run_id, risk_label);

alter table candidate_liabilities
  add column if not exists context_completeness    text,         -- complete | partial | none
  add column if not exists assessed_lane_count     int,
  add column if not exists not_assessed_lane_count int;
-- interpreted_findings / missing_or_unassessed_items stay in raw_payload (JSONB).


-- =====================================================================
-- Step 7: prepared_structure_input_package
-- =====================================================================

create table prepared_structure_packages (
  prepared_structure_package_id text primary key,                 -- uuid5(run_id, step_id)  (one per run)
  run_id                        text not null references runs(run_id) on delete cascade,
  step_record_id                text not null references step_records(step_record_id) on delete cascade,
  package_status                text not null default 'unknown',
  preparation_warnings          jsonb not null default '[]'::jsonb,
  raw_payload                   jsonb not null default '{}'::jsonb,
  created_at                    timestamptz not null default now(),
  unique (run_id)
);
create index idx_prepared_structure_packages_run on prepared_structure_packages (run_id);

create table prepared_structure_inputs (
  prepared_structure_input_id   text primary key,                 -- ULID
  run_id                        text not null references runs(run_id) on delete cascade,
  prepared_structure_package_id text not null references prepared_structure_packages(prepared_structure_package_id) on delete cascade,
  candidate_id                  text,                             -- may be null for pooled inputs
  input_status                  text not null default 'unknown',
  crystal_metadata              jsonb,                            -- compact cell params (a/b/c/alpha.../space_group/z_value/...)
  molecular_weight_estimate     jsonb,                            -- compact MW estimate (value/unit/method/status/...)
  raw_payload                   jsonb not null default '{}'::jsonb,
  created_at                    timestamptz not null default now(),
  foreign key (run_id, candidate_id) references candidates (run_id, candidate_id) on delete cascade
);
create index idx_prepared_structure_inputs_run on prepared_structure_inputs (run_id, candidate_id);

-- Sequence refs for prediction: DIGEST + REF ONLY, never the raw sequence.
create table sequence_refs_for_prediction (
  sequence_ref_id               text primary key,                 -- ULID
  run_id                        text not null references runs(run_id) on delete cascade,
  prepared_structure_package_id text not null references prepared_structure_packages(prepared_structure_package_id) on delete cascade,
  candidate_id                  text,
  chain_role                    text,                             -- heavy | light | antigen | ... (kept distinct)
  sequence_length               int,                              -- persisted length only
  sha256_prefix                 text,                             -- persisted digest prefix only
  artifact_id                   text references artifacts(artifact_id),  -- pointer to the real bytes
  raw_payload                   jsonb not null default '{}'::jsonb,
  created_at                    timestamptz not null default now(),
  foreign key (run_id, candidate_id) references candidates (run_id, candidate_id) on delete cascade
);
create index idx_sequence_refs_run on sequence_refs_for_prediction (run_id, candidate_id);


-- =====================================================================
-- Step 8: structure_prediction_and_interface_results
-- =====================================================================

create table structure_prediction_summaries (
  structure_prediction_summary_id text primary key,               -- uuid5(run_id, step_id)  (one per run)
  run_id                          text not null references runs(run_id) on delete cascade,
  step_record_id                  text not null references step_records(step_record_id) on delete cascade,
  summary_status                  text not null default 'unknown',
  notes                           text[] not null default '{}',
  raw_payload                     jsonb not null default '{}'::jsonb,
  created_at                      timestamptz not null default now(),
  unique (run_id)
);
create index idx_structure_prediction_summaries_run on structure_prediction_summaries (run_id);

create table candidate_structure_results (
  run_id                          text not null references runs(run_id) on delete cascade,
  candidate_id                    text not null,                  -- unique WITHIN a run
  structure_prediction_summary_id text not null references structure_prediction_summaries(structure_prediction_summary_id) on delete cascade,
  complex_prediction_input_status text,
  prediction_runtime_status       text,
  -- downstream_handoff kept whole in JSONB; the two most-filtered booleans are
  -- projected as columns for indexing (index candidates in the v0.3 diff).
  has_complex_structure           boolean not null default false, -- index candidate
  has_validated_structure         boolean not null default false,
  downstream_handoff              jsonb not null default '{}'::jsonb,
  missing_prediction_inputs       text[] not null default '{}',
  prediction_tool_contract_notes  text[] not null default '{}',
  raw_payload                     jsonb not null default '{}'::jsonb,
  created_at                      timestamptz not null default now(),
  primary key (run_id, candidate_id),
  foreign key (run_id, candidate_id) references candidates (run_id, candidate_id)
);
create index idx_candidate_structure_results_complex
  on candidate_structure_results (run_id, has_complex_structure);

create table complex_structure_refs (
  complex_structure_ref_id text primary key,                      -- ULID
  run_id                   text not null references runs(run_id) on delete cascade,
  candidate_id             text not null,
  ref_kind                 text,                                  -- existing | predicted
  pdb_id                   text,                                  -- index candidate
  artifact_id              text references artifacts(artifact_id),
  source_ref               text,
  raw_payload              jsonb not null default '{}'::jsonb,
  created_at               timestamptz not null default now(),
  foreign key (run_id, candidate_id) references candidate_structure_results (run_id, candidate_id) on delete cascade
);
create index idx_complex_structure_refs_pdb on complex_structure_refs (run_id, pdb_id);

create table interface_analysis_records (
  interface_analysis_record_id text primary key,                  -- ULID
  run_id                       text not null references runs(run_id) on delete cascade,
  candidate_id                 text not null,
  record_kind                  text,
  metrics                      jsonb not null default '{}'::jsonb,
  source_tool                  text,
  source_ref                   text,
  raw_payload                  jsonb not null default '{}'::jsonb,
  created_at                   timestamptz not null default now(),
  foreign key (run_id, candidate_id) references candidate_structure_results (run_id, candidate_id) on delete cascade
);
create index idx_interface_analysis_run on interface_analysis_records (run_id, candidate_id);

create table complex_prediction_plans (
  complex_prediction_plan_id text primary key,                    -- ULID (planning audit)
  run_id                     text not null references runs(run_id) on delete cascade,
  candidate_id               text not null,
  plan_status                text,
  plan_detail                jsonb not null default '{}'::jsonb,
  raw_payload                jsonb not null default '{}'::jsonb,
  created_at                 timestamptz not null default now(),
  foreign key (run_id, candidate_id) references candidate_structure_results (run_id, candidate_id) on delete cascade
);


-- =====================================================================
-- Step 9: structure_variant_and_design_results
--   Stage 1 (LLM tool selection) / Stage 2 (schema mapping) / runtime.
--   Same shape as Step 6 selection_audit, promoted to first-class storage.
-- =====================================================================

create table variant_design_summaries (
  variant_design_summary_id       text primary key,               -- uuid5(run_id, step_id) (one per run)
  run_id                          text not null references runs(run_id) on delete cascade,
  step_record_id                  text not null references step_records(step_record_id) on delete cascade,
  input_projection_summary        jsonb not null default '{}'::jsonb,
  projection_missing_inputs       text[] not null default '{}',
  stage1_selection_source         text,                           -- llm | mock | fallback | error | not_run
  stage1_selected_tools           jsonb not null default '[]'::jsonb,
  stage1_rejected_tools           jsonb not null default '[]'::jsonb,
  stage2_mapped_tools             jsonb not null default '[]'::jsonb,
  stage2_uninvokable_tools        text[] not null default '{}',
  stage2_argument_mapping_audit   jsonb not null default '[]'::jsonb,
  runtime_execution_mode          text,                           -- planning_only | executed | not_run
  runtime_executed_tools          text[] not null default '{}',
  raw_payload                     jsonb not null default '{}'::jsonb,
  created_at                      timestamptz not null default now(),
  unique (run_id)
);
create index idx_variant_design_summaries_run on variant_design_summaries (run_id);

-- Centralized compact input inventory. candidate_id optional (some fields are
-- run-level), so this references run_id only and indexes candidate_id.
create table step9_input_fields (
  step9_input_field_id      text primary key,                     -- ULID
  run_id                    text not null references runs(run_id) on delete cascade,
  variant_design_summary_id text not null references variant_design_summaries(variant_design_summary_id) on delete cascade,
  field_ref                 text not null,                        -- index candidate
  candidate_id              text,
  source_step               text,
  source_artifact           text,
  source_path               text,
  field_name                text,
  field_type                text,                                 -- index candidate
  value_kind                text,                                 -- index candidate
  semantic_role             text,
  chain_role                text,
  supports_tool_args        text[] not null default '{}',
  can_resolve_at_runtime    boolean not null default false,
  status                    text,
  missing_reason            text,
  llm_safe_metadata         jsonb not null default '{}'::jsonb,   -- compact, redacted
  raw_payload               jsonb not null default '{}'::jsonb,
  created_at                timestamptz not null default now()
);
create index idx_step9_input_fields_ref  on step9_input_fields (run_id, field_ref);
create index idx_step9_input_fields_kind on step9_input_fields (run_id, field_type, value_kind);
create index idx_step9_input_fields_cand on step9_input_fields (run_id, candidate_id);

create table step9_execution_records (
  step9_execution_record_id text primary key,                     -- ULID
  run_id                    text not null references runs(run_id) on delete cascade,
  variant_design_summary_id text not null references variant_design_summaries(variant_design_summary_id) on delete cascade,
  tool_name                 text not null,                        -- NvidiaNIM_rfdiffusion | NvidiaNIM_proteinmpnn | AlphaMissense_* | DynaMut2_* | ESM_*
  candidate_id              text,
  run_status                text not null,
  started_at                timestamptz,
  finished_at               timestamptz,
  tool_input_summary        jsonb not null default '{}'::jsonb,   -- compact / redacted, NOT raw payload
  tool_output_artifact_id   text references artifacts(artifact_id),
  tool_output_ref           text,
  error_message             text,
  raw_payload               jsonb not null default '{}'::jsonb,
  created_at                timestamptz not null default now()
);
create index idx_step9_exec_run  on step9_execution_records (run_id, tool_name);
create index idx_step9_exec_tool on step9_execution_records (tool_name, run_status);


-- =====================================================================
-- Shared: pipeline snapshot / hydrate manifest storage
--   Refs / paths / hashes / sizes ONLY — never embedded raw bytes.
-- =====================================================================

create table pipeline_snapshots (
  snapshot_id             text primary key,                       -- ULID
  source_run_id           text not null references runs(run_id) on delete cascade,
  snapshot_version        text not null,
  hydrated_from_snapshot_id text references pipeline_snapshots(snapshot_id),
  through_step            text,                                   -- last step captured, e.g. 'step_08_...'
  completed_steps         text[] not null default '{}',
  workflow_state_summary  jsonb not null default '{}'::jsonb,
  registry_summary        jsonb not null default '{}'::jsonb,
  created_at              timestamptz not null default now()
);
create index idx_pipeline_snapshots_run on pipeline_snapshots (source_run_id, created_at desc);

create table snapshot_artifact_entries (
  snapshot_artifact_entry_id text primary key,                    -- ULID
  snapshot_id                text not null references pipeline_snapshots(snapshot_id) on delete cascade,
  entry_kind                 text not null,                       -- active_artifact | uploaded_file | tool_output_file
  artifact_id                text references artifacts(artifact_id),
  object_path                text,                                -- ref only
  sha256                     text,
  size_bytes                 bigint,
  raw_payload                jsonb not null default '{}'::jsonb,
  created_at                 timestamptz not null default now()
);
create index idx_snapshot_entries_snapshot on snapshot_artifact_entries (snapshot_id, entry_kind);


-- =====================================================================
-- Idempotency: additions required in app/db/projection.py::_OWNED
-- (kept here as documentation; the Python dict is the runtime source).
--
--   "step_02_structured_query": [ ..., ("query_missing_slots", "") ],  # new child of structured_queries
--       (already cascades via structured_queries delete; explicit entry optional)
--   "step_03_input_readiness":  [ ..., ("clarification_requests", "") ],
--       (already cascades via input_readiness delete; explicit entry optional)
--   "step_07_structure_prep": [
--        ("prepared_structure_packages", ""),   # -> prepared_structure_inputs, sequence_refs_for_prediction
--   ],
--   "step_08_structure_prediction": [
--        ("structure_prediction_summaries", ""),# -> candidate_structure_results -> refs/interface/plans
--   ],
--   "step_09_variant_design": [
--        ("variant_design_summaries", ""),      # -> step9_input_fields, step9_execution_records
--   ],
--
-- Snapshots are NOT step projections: they are created explicitly and are
-- immutable, so they are never wiped by reset_step_projection.
-- =====================================================================
