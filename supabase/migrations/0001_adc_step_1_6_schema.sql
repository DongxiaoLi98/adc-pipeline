-- =====================================================================
-- ADC Pipeline — Step 1-6 schema (single source of truth)
-- Migration 0001
--
-- Design decisions baked into this file (see docs/01_data_model.md):
--   * Run-scoped composite primary keys for the candidate/material graph
--     (candidates, materials, uploaded_files, candidate_liabilities,
--      lane_results). IDs like "cand_001" are only unique WITHIN a run.
--   * Global ULID single-column keys for cross-cutting registries
--     (artifacts, tool_call_records) and per-step summary rows.
--   * artifacts carries backend/bucket/object_key/upload_status/etag so we
--     can build presigned URLs and reconcile object-vs-DB write failures.
--   * Enums are TEXT now (values still drifting per the schema delta);
--     CHECK constraints added only once a value set is frozen.
--   * on delete cascade is used so reset_step_projection() can wipe a
--     step's owned rows by deleting the root row.
-- =====================================================================

-- ---------- run + step backbone ----------

create table runs (
  run_id           text primary key,
  project_id       text,
  user_id          text,
  run_status       text not null default 'created',
  current_step_id  text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  completed_at     timestamptz,
  error_message    text,
  schema_version   text not null default 'adc_pipeline_schema_v0_1_local',
  pipeline_version text,
  metadata         jsonb not null default '{}'::jsonb
);
create index idx_runs_status_created_at on runs (run_status, created_at desc);

create table step_records (
  step_record_id         text primary key,                       -- deterministic uuid5(run_id, step_id)
  run_id                 text not null references runs(run_id) on delete cascade,
  step_id                text not null,
  step_order             int  not null,
  step_status            text not null default 'pending',
  source_step_record_ids text[] not null default '{}',
  input_payload          jsonb not null default '{}'::jsonb,
  output_payload         jsonb not null default '{}'::jsonb,      -- full model_dump(): the fallback of record
  started_at             timestamptz,
  finished_at            timestamptz,
  error_message          text,
  schema_version         text not null,
  model_version          text,
  code_commit_sha        text,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  unique (run_id, step_id)                                        -- makes the step record idempotent
);
create index idx_step_records_run_step on step_records (run_id, step_id);
create index idx_step_records_status   on step_records (step_status);

-- ---------- artifact registry (the single pointer table) ----------

create table artifacts (
  artifact_id            text primary key,                        -- global ULID
  run_id                 text not null references runs(run_id) on delete cascade,
  producing_step_id      text,
  producing_tool_call_id text,
  artifact_type          text not null,                           -- uploaded_file | tool_output | report | analytics_export
  storage_backend        text not null default 'local',           -- local | supabase_storage | localstack_s3 | aws_s3
  bucket                 text,
  object_key             text,
  storage_uri            text not null,                            -- human-readable convenience mirror of backend/bucket/key
  upload_status          text not null default 'pending',         -- pending | available | failed  (see docs/02 lifecycle)
  content_type           text,
  sha256                 text,
  etag                   text,
  size_bytes             bigint,
  is_active              boolean not null default true,
  parent_artifact_ids    text[] not null default '{}',
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  metadata               jsonb not null default '{}'::jsonb
);
create index idx_artifacts_run    on artifacts (run_id);
create index idx_artifacts_type   on artifacts (artifact_type);
create index idx_artifacts_sha    on artifacts (sha256);
create index idx_artifacts_status on artifacts (upload_status);

-- ---------- Step 1: raw request record ----------

create table raw_requests (
  raw_request_record_id text primary key,
  run_id                text not null references runs(run_id) on delete cascade,
  step_record_id        text not null references step_records(step_record_id) on delete cascade,
  entry_source          text not null,
  submitted_by          text,
  raw_user_query        text not null,
  intake_status         text not null,
  received_at           timestamptz not null default now(),
  raw_payload           jsonb not null default '{}'::jsonb,
  unique (run_id)                                                 -- one raw request per run
);
create index idx_raw_requests_run on raw_requests (run_id);

create table uploaded_files (
  run_id                text not null references runs(run_id) on delete cascade,
  file_id               text not null,                           -- unique WITHIN a run
  raw_request_record_id text references raw_requests(raw_request_record_id) on delete cascade,
  artifact_id           text references artifacts(artifact_id),  -- canonical pointer
  original_filename     text not null,
  storage_path          text not null,                           -- raw path kept for troubleshooting
  content_type          text,
  sha256                text,
  size_bytes            bigint,
  related_candidate_id  text,
  related_candidate_ids text[] not null default '{}',
  role                  text,
  chain_role            text,
  chain_id              text,
  chain_roles           jsonb not null default '{}'::jsonb,
  created_at            timestamptz not null default now(),
  metadata              jsonb not null default '{}'::jsonb,
  primary key (run_id, file_id)
);
create index idx_uploaded_files_run on uploaded_files (run_id);

-- ---------- Step 2: structured query ----------

create table structured_queries (
  structured_query_id        text primary key,
  run_id                     text not null references runs(run_id) on delete cascade,
  step_record_id             text not null references step_records(step_record_id) on delete cascade,
  raw_request_record_id      text references raw_requests(raw_request_record_id),
  primary_intent             text not null,
  primary_intent_confidence  numeric,
  secondary_intents          text[] not null default '{}',
  task_type                  text,
  task_type_confidence       numeric,
  modality                   text,
  modality_confidence        numeric,
  user_goal_summary          text,
  target_or_antigen_text     text,
  disease_or_indication_text text,
  antibody_candidate_text    text,
  payload_text               text,
  linker_text                text,
  referenced_inputs          jsonb not null default '[]'::jsonb,
  requested_outputs          text[] not null default '{}',
  user_constraints           jsonb not null default '[]'::jsonb,
  parse_warnings             text[] not null default '{}',
  clarification_questions    text[] not null default '{}',
  raw_payload                jsonb not null default '{}'::jsonb,
  created_at                 timestamptz not null default now(),
  unique (run_id)
);
create index idx_structured_queries_run            on structured_queries (run_id);
create index idx_structured_queries_primary_intent on structured_queries (primary_intent);

create table normalized_entities (
  normalized_entity_id text primary key,                          -- ULID
  run_id               text not null references runs(run_id) on delete cascade,
  structured_query_id  text not null references structured_queries(structured_query_id) on delete cascade,
  original_text        text not null,
  canonical_name       text,
  canonical_id         text,
  canonical_id_source  text,
  entity_type          text not null default 'other',
  explicit_or_inferred text not null default 'inferred',
  confidence           numeric not null default 0.0,
  notes                text,
  created_at           timestamptz not null default now()
);
create index idx_normalized_entities_run_type  on normalized_entities (run_id, entity_type);
create index idx_normalized_entities_canonical on normalized_entities (canonical_name);

create table entity_decompositions (
  entity_decomposition_id text primary key,                       -- ULID
  run_id                  text not null references runs(run_id) on delete cascade,
  structured_query_id     text not null references structured_queries(structured_query_id) on delete cascade,
  original_text           text not null,
  canonical_name          text,
  notes                   text,
  created_at              timestamptz not null default now()
);

create table entity_components (
  entity_component_id     text primary key,                       -- ULID
  entity_decomposition_id text not null references entity_decompositions(entity_decomposition_id) on delete cascade,
  role                    text not null default 'other',
  canonical_name          text not null,
  component_type          text,
  canonical_id            text,
  canonical_id_source     text,
  inferred                boolean not null default true,
  source                  text,
  notes                   text
);

-- ---------- Step 3: input readiness ----------

create table input_readiness (
  input_readiness_id            text primary key,
  run_id                        text not null references runs(run_id) on delete cascade,
  step_record_id                text not null references step_records(step_record_id) on delete cascade,
  structured_query_id           text references structured_queries(structured_query_id),
  target_or_antigen_present     boolean not null default false,
  antibody_candidate_present    boolean not null default false,
  payload_present               boolean not null default false,
  linker_present                boolean not null default false,
  structure_or_sequence_present boolean not null default false,
  constraints_present           boolean not null default false,
  adc_task_intent_present       boolean not null default false,
  structure_input_present       boolean not null default false,
  sequence_input_present        boolean not null default false,
  candidate_file_present        boolean not null default false,
  evidence                      jsonb not null default '{}'::jsonb,
  blocking_reasons              text[] not null default '{}',
  readiness_status              text not null default 'unknown',
  raw_payload                   jsonb not null default '{}'::jsonb,
  created_at                    timestamptz not null default now(),
  unique (run_id)
);
create index idx_input_readiness_run    on input_readiness (run_id);
create index idx_input_readiness_status on input_readiness (readiness_status);

create table missing_input_items (
  missing_input_item_id text primary key,                         -- ULID
  run_id                text not null references runs(run_id) on delete cascade,
  input_readiness_id    text not null references input_readiness(input_readiness_id) on delete cascade,
  field                 text not null,
  severity              text not null,                            -- blocking | warning | optional (TEXT for now)
  message               text not null,
  category              text,
  evidence_field        text,
  can_continue          boolean not null default true,            -- written by app layer, NOT a generated column
  created_at            timestamptz not null default now()
);
create index idx_missing_input_items_run_severity on missing_input_items (run_id, severity);

create table uploaded_file_checks (
  uploaded_file_check_id text primary key,                        -- ULID
  run_id                 text not null references runs(run_id) on delete cascade,
  input_readiness_id     text not null references input_readiness(input_readiness_id) on delete cascade,
  file_id                text,
  file_exists            boolean,                                 -- renamed from `exists` (reserved word)
  checksum_ok            boolean,
  format_ok              boolean,
  inferred_role          text,
  storage_path_present   boolean,
  content_type_present   boolean,
  size_bytes_present     boolean,
  notes                  text,
  created_at             timestamptz not null default now(),
  foreign key (run_id, file_id) references uploaded_files (run_id, file_id) on delete cascade
);

-- ---------- Step 5: candidate context ----------

create table candidate_contexts (
  candidate_context_id       text primary key,
  run_id                     text not null references runs(run_id) on delete cascade,
  step_record_id             text not null references step_records(step_record_id) on delete cascade,
  context_build_status       text not null,
  downstream_query_hints     jsonb not null default '{}'::jsonb,
  enrichment_selection_audit jsonb not null default '{}'::jsonb,
  raw_payload                jsonb not null default '{}'::jsonb,
  created_at                 timestamptz not null default now(),
  unique (run_id)
);

create table candidates (
  run_id                 text not null references runs(run_id) on delete cascade,
  candidate_id           text not null,                           -- unique WITHIN a run
  candidate_context_id   text references candidate_contexts(candidate_context_id) on delete cascade,
  candidate_name         text,
  candidate_role         text not null default 'unknown',
  is_generated_candidate boolean not null default false,
  context_status         text not null default 'unknown',
  candidate_status       text,
  source_records         text[] not null default '{}',
  data_gaps              text[] not null default '{}',
  missing_material_roles text[] not null default '{}',
  context_notes          text[] not null default '{}',
  raw_payload            jsonb not null default '{}'::jsonb,
  created_at             timestamptz not null default now(),
  primary key (run_id, candidate_id)
);
create index idx_candidates_run    on candidates (run_id);
create index idx_candidates_status on candidates (run_id, candidate_status);

create table materials (
  run_id            text not null references runs(run_id) on delete cascade,
  material_id       text not null,                                -- unique WITHIN a run
  material_type     text not null,
  value_inline      text,                                         -- small values (e.g. SMILES) inline
  value_artifact_id text references artifacts(artifact_id),       -- large values -> object store pointer
  value_format      text,
  extraction_status text not null default 'pending',
  validation_status text not null default 'unknown',
  role              text,
  role_status       text not null default 'unknown',
  raw_payload       jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  primary key (run_id, material_id)
  -- app-level rule: exactly one of value_inline / value_artifact_id is set
);
create index idx_materials_run_type on materials (run_id, material_type);

create table candidate_identifiers (
  candidate_identifier_id text primary key,                       -- ULID
  run_id                  text not null references runs(run_id) on delete cascade,
  candidate_id            text not null,
  id_type                 text not null,
  id_value                text not null,
  source_ids              text[] not null default '{}',
  confidence              numeric not null default 0.0,
  created_at              timestamptz not null default now(),
  foreign key (run_id, candidate_id) references candidates (run_id, candidate_id) on delete cascade
);
create index idx_candidate_identifiers_lookup on candidate_identifiers (id_type, id_value);

create table candidate_material_links (
  candidate_material_link_id text primary key,                    -- ULID
  run_id                     text not null references runs(run_id) on delete cascade,
  candidate_id               text not null,
  material_id                text not null,
  material_role              text not null,
  link_status                text not null default 'active',
  created_at                 timestamptz not null default now(),
  foreign key (run_id, candidate_id) references candidates (run_id, candidate_id) on delete cascade,
  foreign key (run_id, material_id)  references materials  (run_id, material_id)  on delete cascade,
  unique (run_id, candidate_id, material_id, material_role)
);
create index idx_cml_candidate on candidate_material_links (run_id, candidate_id);
create index idx_cml_material  on candidate_material_links (run_id, material_id);

-- ---------- Step 6: structured liability summary ----------

create table liability_summaries (
  liability_summary_id     text primary key,
  run_id                   text not null references runs(run_id) on delete cascade,
  step_record_id           text not null references step_records(step_record_id) on delete cascade,
  prefilter_status         text not null,
  tool_output_artifact_ids text[] not null default '{}',
  notes                    text[] not null default '{}',
  selection_audit          jsonb not null default '{}'::jsonb,
  raw_payload              jsonb not null default '{}'::jsonb,
  created_at               timestamptz not null default now(),
  unique (run_id)
);
create index idx_liability_summaries_run on liability_summaries (run_id);

create table candidate_liabilities (
  run_id                            text not null references runs(run_id) on delete cascade,
  candidate_id                      text not null,
  liability_summary_id              text not null references liability_summaries(liability_summary_id) on delete cascade,
  candidate_prefilter_status        text not null,
  candidate_overall_liability_label text not null,
  recommended_action                text,
  -- candidate_summary intentionally omitted (not in current code); see raw_payload
  raw_payload                       jsonb not null default '{}'::jsonb,
  created_at                        timestamptz not null default now(),
  primary key (run_id, candidate_id),
  foreign key (run_id, candidate_id) references candidates (run_id, candidate_id)
);
create index idx_candidate_liabilities_run_label
  on candidate_liabilities (run_id, candidate_overall_liability_label);

create table lane_results (
  run_id                 text not null references runs(run_id) on delete cascade,
  candidate_id           text not null,
  lane_type              text not null,
  run_status             text not null,
  input_status           text not null,
  selected_tools         text[] not null default '{}',
  argument_mapping_audit jsonb not null default '[]'::jsonb,
  lane_risk_category     text not null default 'unknown',
  lane_summary           text,
  raw_payload            jsonb not null default '{}'::jsonb,
  created_at             timestamptz not null default now(),
  primary key (run_id, candidate_id, lane_type),
  foreign key (run_id, candidate_id) references candidate_liabilities (run_id, candidate_id) on delete cascade
);
create index idx_lane_results_run_type on lane_results (run_id, lane_type);

create table liability_flags (
  liability_flag_id        text primary key,                      -- ULID
  run_id                   text not null references runs(run_id) on delete cascade,
  candidate_id             text not null,
  lane_type                text not null,
  flag_type                text,
  severity                 text,
  risk_category            text,
  evidence_summary         text,
  source_tool              text,
  source_ref               text,
  supporting_tool_call_ids text[] not null default '{}',
  raw_flag                 jsonb not null default '{}'::jsonb,
  created_at               timestamptz not null default now(),
  foreign key (run_id, candidate_id, lane_type)
    references lane_results (run_id, candidate_id, lane_type) on delete cascade
);
create index idx_liability_flags_run_severity on liability_flags (run_id, severity);

-- tool_call_records: treated as the CURRENT-STEP PROJECTION (rebuilt on same-run rerun).
-- Immutable history, if needed, is written separately to audit_events (see docs/04).
create table tool_call_records (
  run_id                  text not null references runs(run_id) on delete cascade,
  tool_call_id            text not null,                          -- unique WITHIN a run
  step_id                 text,
  candidate_id            text,
  lane_type               text,
  tool_name               text not null,
  agent_name              text,
  run_status              text not null,
  started_at              timestamptz,
  finished_at             timestamptz,
  idempotency_key         text,
  tool_input_summary      jsonb not null default '{}'::jsonb,
  tool_output_artifact_id text references artifacts(artifact_id),
  tool_output_ref         text,
  error_message           text,
  created_at              timestamptz not null default now(),
  primary key (run_id, tool_call_id)
);
create index idx_tcr_run  on tool_call_records (run_id, step_id);
create index idx_tcr_tool on tool_call_records (tool_name, run_status);
create unique index uq_tcr_idem
  on tool_call_records (run_id, idempotency_key)
  where idempotency_key is not null;

-- ---------- ops / governance (see docs/04) ----------

create table audit_events (
  audit_event_id text primary key,                                -- ULID
  run_id         text references runs(run_id) on delete cascade,
  actor          text,
  action         text not null,
  target_type    text,
  target_id      text,
  event_payload  jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now()
);
create index idx_audit_events_run on audit_events (run_id, created_at desc);

create table human_reviews (
  human_review_id text primary key,                               -- ULID
  run_id          text not null references runs(run_id) on delete cascade,
  candidate_id    text,
  review_status   text not null default 'pending',
  reviewer_id     text,
  decision        text,
  notes           text,
  created_at      timestamptz not null default now(),
  reviewed_at     timestamptz,
  foreign key (run_id, candidate_id) references candidates (run_id, candidate_id)
);

create table step_errors (
  step_error_id text primary key,                                 -- ULID
  run_id        text not null references runs(run_id) on delete cascade,
  step_id       text,
  tool_call_id  text,
  error_type    text,
  error_message text,
  traceback     text,
  retry_count   int not null default 0,
  created_at    timestamptz not null default now()
);
create index idx_step_errors_run on step_errors (run_id);
