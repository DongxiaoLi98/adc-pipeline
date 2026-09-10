# Database-Oriented Schema Diff: ADC Pipeline IO Schema v0.2 -> v0.3

Date: 2026-07-07

Audience: database / storage implementation owner

Purpose: identify the Step 1-9 schema differences that may require database JSON schema, table column, JSONB path, validation, index, or artifact-storage updates.

Storage assumption:

- If pipeline artifacts are stored as JSONB blobs, most changes are JSON schema / validation changes rather than relational column migrations.
- If selected artifact fields are indexed or projected into relational columns, the fields marked “index candidate” below should be considered for explicit columns or JSONB indexes.
- Do not store raw PDB/CIF/FASTA/protein sequence/A3M, raw ToolUniverse payload, API keys, full prompts, or raw LLM responses in normalized artifact tables.

## Migration Summary

| Area | DB action |
|---|---|
| Step 1 | Add clarification revision fields under `user_provided_context`. |
| Step 2 | Add `canonical_query`, `missing_slots`, `response`; update enum validation for variants and prompt sequence. |
| Step 3 | Add `clarification_requests`, `response`. |
| Step 4 | No schema migration required. |
| Step 5 | Add `Material.content_descriptor`; support variant / prompt-sequence / heavy-light sequence semantics. |
| Step 6 | Add lane and candidate interpretation fields. |
| Step 7 | Add crystal metadata, molecular-weight estimate, sequence digest fields, and preparation warnings. |
| Step 8 | Add complex structure refs, interface records, prediction plans, and downstream handoff. |
| Step 9 | Replace old branch-style Step 9 storage contract with input projection + LLM Stage 1/2 + runtime execution records. |
| Snapshot / Hydrate | Add new shared snapshot manifest artifact storage if not already present. |

## Step 1: `raw_request_record`

Artifact/table: `raw_request_record`

Path to update: `user_provided_context`

### Add fields

| JSON path | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `user_provided_context.previous_task_intent` | object | yes | no | Prior Step 2 task intent for clarification revision runs. |
| `user_provided_context.previous_missing_slots` | array<object> | yes | no | Prior Step 2 missing slots. |
| `user_provided_context.previous_clarification_requests` | array<object> | yes | no | Prior Step 3 clarification requests. |
| `user_provided_context.clarification_answers` | array<object> | yes | no | User answers collected by clarification service. |
| `user_provided_context.previous_canonical_query` | string | yes | maybe | Useful for tracing revision runs. |

### DB impact

- Existing rows can default these fields to null or empty arrays.
- If revision runs are queried often, consider indexing `run_id` + `previous_canonical_query` only if needed.

## Step 2: `structured_query`

Artifact/table: `structured_query`

### Add fields

| JSON path | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `canonical_query` | string | yes | maybe | Preferred downstream natural-language task summary. |
| `missing_slots` | array<object> | yes / default `[]` | yes | Machine-readable missing input list. |
| `response` | string | yes | no | User-facing clarification text. |

### `missing_slots[]` object shape

| Field | Suggested type | Nullable | Notes |
|---|---:|---:|---|
| `slot_name` | string enum | no | Includes `target_or_antigen`, `antibody`, `payload`, `linker`, `sequence_role`, `prompt_sequence`, `other`. |
| `slot_category` | string | no | Grouping for UI / readiness. |
| `severity` | string enum | no | `blocking`, `warning`, `optional`. |
| `required_for` | array<string> | yes | Tool/lane/pipeline dependency. |
| `reason` | string | yes | Compact reason. |
| `suggested_question` | string | yes | UI prompt. |
| `evidence` | object | yes | Compact evidence only. |

### Enum / validation updates

| Existing area | v0.3 update |
|---|---|
| `referenced_inputs[].id_type` | Must allow `variant`, `prompt_sequence`, `antibody_heavy_chain_sequence`, `antibody_light_chain_sequence`. |
| `normalized_entities[].entity_type` | Must allow `protein_variant`. |
| missing slot names | Must allow `prompt_sequence`. |

### DB impact

- Consider JSONB GIN or path indexes on `missing_slots[].slot_name` and `missing_slots[].severity` if the frontend frequently queries blocked runs.
- `canonical_query` may be useful as a projected column for audit/search, but should not replace `raw_user_query`.

## Step 3: `input_readiness_status`

Artifact/table: `input_readiness_status`

### Add fields

| JSON path | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `clarification_requests` | array<object> | yes / default `[]` | yes | Machine-readable questions derived from Step 2 `missing_slots`. |
| `response` | string | yes | no | User-facing response text. |

### `clarification_requests[]` object shape

| Field | Suggested type | Nullable | Notes |
|---|---:|---:|---|
| `request_id` | string | no | Stable request ID. |
| `slot_name` | string | no | Mirrors Step 2 slot name. |
| `slot_category` | string | no | UI/readiness category. |
| `severity` | string enum | no | `blocking`, `warning`, `optional`. |
| `question` | string | no | User-facing question. |
| `reason` | string | yes | Compact reason. |
| `source` | string | yes | Usually `step_02_missing_slots` or checklist source. |
| `evidence_field` | string | yes | Compact field reference. |
| `resolved` | boolean | no / default false | Clarification tracking. |

### DB impact

- Index `input_readiness_status` and `clarification_requests[].severity` if the UI lists blocked/needs-input runs.
- Existing v0.2 rows can default `clarification_requests=[]` and `response=null`.

## Step 4: `run_step_plan`

No database schema change identified for v0.3.

## Step 5: `candidate_context_table`

Artifact/table: `candidate_context_table`

### Add fields

| JSON path | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `candidate_records[].materials[].content_descriptor` | object | yes | no | Compact descriptor for material content without raw payload. |

### Validation / enum updates

| Area | v0.3 update |
|---|---|
| `candidate_records[].identifiers[].id_type` | Must allow `variant`. |
| `candidate_records[].materials[].material_type` | Must allow explicit `antibody_heavy_chain_sequence`, `antibody_light_chain_sequence`; generic antibody sequence refs should not be treated as executable heavy/light chains. |
| Prompt sequence handling | Masked `prompt_sequence` should remain distinct from ordinary complete protein sequence. |

### DB impact

- If identifiers are indexed, add support for `id_type='variant'`.
- If sequence materials are projected into relational columns, heavy and light chain roles must remain distinct.
- Do not store full raw FASTA/protein sequence in general-purpose candidate projection tables; use refs/digests where applicable.

## Step 6: `structured_liability_summary`

Artifact/table: `structured_liability_summary`

### Add fields under `candidate_liability_results[].lane_results[]`

| JSON path suffix | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `assessment_status` | string enum | yes | maybe | `assessed`, `no_signal`, `signal_detected`, `not_assessed_missing_input`, `not_assessed_dependency_unavailable`, `partial_upstream_error`, `failed`. |
| `risk_label` | string enum | yes | yes | `low`, `review`, `high`, `unknown`, `not_assessed`. Useful for filtering. |
| `not_assessed_reason` | string | yes | no | Compact reason. |
| `interpreted_findings` | array<object> | yes / default `[]` | no | Compact findings with source refs. |
| `missing_or_unassessed_items` | array<object> | yes / default `[]` | no | Structured missing/unassessed lane items. |

### Add fields under `candidate_liability_results[]`

| JSON path suffix | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `context_completeness` | string enum | yes | maybe | `complete`, `partial`, `none`. |
| `assessed_lane_count` | integer | yes | no | Count of assessed lanes. |
| `not_assessed_lane_count` | integer | yes | no | Count of not-assessed lanes. |
| `interpretation_summary` | string | yes | no | Compact summary. |
| `missing_or_unassessed_items` | array<object> | yes / default `[]` | no | Candidate-level aggregation. |

### DB impact

- If candidate risk dashboards exist, index candidate-level label fields and lane-level `risk_label`.
- Do not store raw tool outputs in these fields; keep `tool_call_records[].tool_output_ref` as the raw output pointer.

## Step 7: `prepared_structure_input_package`

Artifact/table: `prepared_structure_input_package`

### Add fields under `prepared_structure_inputs[]`

| JSON path suffix | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `crystal_metadata` | object | yes | no | Compact PDB/CIF cell metadata. |
| `molecular_weight_estimate` | object | yes | no | Compact MW estimate. |

### `crystal_metadata` shape

| Field | Suggested type | Nullable |
|---|---:|---:|
| `a`, `b`, `c` | number | yes |
| `alpha`, `beta`, `gamma` | number | yes |
| `space_group` | string | yes |
| `z_value` | integer | yes |
| `source_kind` | string | yes |
| `source_ref` | string | yes |
| `parse_status` | string | yes |
| `warnings` | array<string> | yes / default `[]` |

### `molecular_weight_estimate` shape

| Field | Suggested type | Nullable |
|---|---:|---:|
| `value` | number | yes |
| `unit` | string | yes |
| `method` | string | yes |
| `status` | string | yes |
| `warnings` | array<string> | yes / default `[]` |
| `source_kind` | string | yes |
| `source_ref` | string | yes |

### Add fields under `sequence_refs_for_prediction[]`

| JSON path suffix | Suggested type | Nullable | Notes |
|---|---:|---:|---|
| `sequence_length` | integer | yes | Persisted length only. |
| `sha256_prefix` | string | yes | Persisted digest prefix only. |

### Add package-level field

| JSON path | Suggested type | Nullable | Notes |
|---|---:|---:|---|
| `preparation_warnings` | array<object> | yes / default `[]` | Compact warnings for selected tool or semantic preparation failures. |

### DB impact

- If Step 8 validation queries `a`, `z_value`, or MW, consider JSON path indexes only if performance requires it.
- Do not persist raw `SequenceRef.sequence` in normalized artifact JSON. Use `sequence_length`, `sha256_prefix`, and storage refs.

## Step 8: `structure_prediction_and_interface_results`

Artifact/table: `structure_prediction_and_interface_results`

### Add fields under `candidate_structure_results[]`

| JSON path suffix | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `complex_structure_refs` | array<object> | yes / default `[]` | yes | Compact refs to existing/predicted complex structures. |
| `interface_analysis_records` | array<object> | yes / default `[]` | no | Normalized interface-analysis records. |
| `downstream_handoff` | object | yes | yes | Step 9 handoff contract. |
| `complex_prediction_plans` | array<object> | yes / default `[]` | no | Prediction planning audit. |
| `complex_prediction_input_status` | string | yes | maybe | Prediction readiness status. |
| `missing_prediction_inputs` | array<string> | yes / default `[]` | no | Missing sequence/MSA/etc. |
| `prediction_runtime_status` | string | yes | maybe | Runtime status. |
| `prediction_tool_contract_notes` | array<string> | yes / default `[]` | no | Compact contract notes. |

### `downstream_handoff` shape

| Field | Suggested type | Nullable | Index candidate |
|---|---:|---:|---:|
| `has_complex_structure` | boolean | no / default false | yes |
| `has_validated_structure` | boolean | no / default false | maybe |
| `has_interface_features` | boolean | no / default false | maybe |
| `structure_for_variant_generation_ref` | string | yes | maybe |
| `validated_structure_ref` | string | yes | maybe |
| `interface_quality_available` | boolean | no / default false | no |
| `prediction_confidence_available` | boolean | no / default false | no |
| `refinement_resolution_available` | boolean | no / default false | no |
| `validation_available` | boolean | no / default false | no |
| `missing_for_step9` | array<string> | yes / default `[]` | no |
| `handoff_notes` | array<string> | yes / default `[]` | no |

### DB impact

- Step 9 depends heavily on `candidate_structure_results[].downstream_handoff` and `complex_structure_refs`.
- Consider indexing `downstream_handoff.has_complex_structure` and `complex_structure_refs[].pdb_id` if Step 9 or UI frequently filters by true complex availability.
- Uploaded/local structure validation alone must not be interpreted as `has_complex_structure=true`.

## Step 9: `structure_variant_and_design_results`

Artifact/table: Step 9 output artifact. Current code may still use an older class name internally, but storage should target the v0.3 active contract below.

### Replace old v0.2 Step 9 branch schema

v0.2 had a branch-oriented design for protein structural variants and compound/library screening. v0.3 active storage should use:

- centralized input projection,
- Stage 1 LLM tool selection,
- Stage 2 schema mapping,
- runtime execution records.

### Add active fields

| JSON path | Suggested type | Nullable | Index candidate | Notes |
|---|---:|---:|---:|---|
| `step9_input_fields` | array<object> | yes / default `[]` | yes | Centralized compact input inventory. |
| `step9_input_projection_summary` | object | yes | no | Compact projection summary. |
| `step9_projection_missing_inputs` | array<string> | yes / default `[]` | no | Missing projection inputs. |
| `step9_stage1_selected_tools` | array<object> | yes / default `[]` | maybe | LLM-selected active tools. |
| `step9_stage1_rejected_tools_with_reason` | array<object> | yes / default `[]` | no | Rejected tool audit. |
| `step9_stage1_selection_source` | string | yes | maybe | `llm`, `mock`, `fallback`, `error`, `not_run`, etc. |
| `step9_stage2_mapped_tools` | array<object> | yes / default `[]` | maybe | Valid schema mappings. |
| `step9_stage2_uninvokable_tools` | array<string> | yes / default `[]` | maybe | Selected tools that cannot be invoked. |
| `step9_stage2_uninvokable_tool_details` | array<object> | yes / default `[]` | no | Missing/invalid mapping reasons. |
| `step9_stage2_argument_mapping_audit` | array<object> | yes / default `[]` | no | Compact mapping audit. |
| `step9_runtime_execution_mode` | string | yes | maybe | `planning_only`, `executed`, `not_run`, etc. |
| `step9_runtime_executed_tools` | array<string> | yes / default `[]` | maybe | Executed active tool names. |
| `step9_runtime_execution_records` | array<object> | yes / default `[]` | yes | Compact live execution records. |

### `step9_input_fields[]` shape

| Field | Suggested type | Nullable | Index candidate |
|---|---:|---:|---:|
| `field_ref` | string | no | yes |
| `candidate_id` | string | yes | yes |
| `candidate_ids` | array<string> | yes / default `[]` | maybe |
| `source_step` | string enum | no | maybe |
| `source_steps` | array<string> | yes / default `[]` | no |
| `source_artifact` | string | no | no |
| `source_path` | string | no | no |
| `field_name` | string | no | no |
| `field_type` | string | no | yes |
| `value_kind` | string | no | yes |
| `semantic_role` | string | yes | no |
| `chain_role` | string | yes | maybe |
| `supports_tool_args` | array<string> | yes / default `[]` | maybe |
| `can_resolve_at_runtime` | boolean | no / default false | maybe |
| `llm_safe_metadata` | object | yes | no |
| `runtime_lookup` | object | yes | no |
| `status` | string enum | no | maybe |
| `missing_reason` | string | yes | no |

### Active Step 9 tools

Storage validation should allow records for:

- `NvidiaNIM_rfdiffusion`
- `NvidiaNIM_proteinmpnn`
- `AlphaMissense_get_variant_score`
- `DynaMut2_predict_stability`
- `ESM_score_variant_sae_batch`
- `ESM_generate_protein_sequence`

### DB impact

- Prefer indexing `step9_input_fields[].field_ref`, `step9_input_fields[].field_type`, `step9_input_fields[].value_kind`, and `step9_runtime_executed_tools` if Step 9 run inspection is common.
- `step9_runtime_execution_records[].tool_input_summary` must be compact/redacted.
- Do not store raw structure text, protein sequence, A3M alignments, API keys, full prompts, or raw LLM responses in Step 9 normalized records.

## Shared Pipeline Snapshot / Hydrate

This is new infrastructure and not tied to Step 10+.

Artifact/table: snapshot manifest storage.

### Add artifact type / manifest schema

Current models:

- `PipelineSnapshotManifest`
- `ArtifactSnapshotEntry`
- `UploadedFileSnapshotEntry`

Important fields:

- `snapshot_id`
- `snapshot_version`
- `source_run_id`
- `hydrated_from_snapshot_id`
- `through_step`
- `completed_steps`
- `active_artifacts`
- `artifact_files`
- `uploaded_files`
- `tool_output_files`
- `workflow_state_summary`
- `registry_summary`

### DB impact

- Snapshot manifests should store refs, paths, hashes, and sizes.
- Snapshot manifests should not embed raw uploaded files, raw tool payloads, full PDB/CIF/FASTA/protein sequence/A3M content, or API keys.
