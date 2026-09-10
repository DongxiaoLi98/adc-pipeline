"""Idempotency: make every step write "rerun == run-once".

The step_records row is upserted on (run_id, step_id). Its child projection
rows are NOT upserted individually; instead, before rewriting a step we delete
that step's OWNED root rows for the run, and on-delete-cascade wipes the detail
rows underneath. Then the writer inserts a fresh set. Running a step twice
therefore yields the same rows as running it once.

Ownership map (which root delete cascades to what) lives in
docs/01_data_model.md and mirrors the FKs in migration 0001.
"""
from __future__ import annotations

from sqlalchemy import text

# Each entry: list of (table, extra_where) deleted by run_id. Cascades handle details.
_OWNED: dict[str, list[tuple[str, str]]] = {
    "step_01_intake": [
        ("raw_requests", ""),                                   # -> uploaded_files -> uploaded_file_checks
        ("artifacts", "and artifact_type = 'uploaded_file'"),
    ],
    "step_02_structured_query": [
        ("structured_queries", ""),                             # -> normalized_entities, entity_decompositions -> components
    ],
    "step_03_input_readiness": [
        ("input_readiness", ""),                                # -> missing_input_items, uploaded_file_checks
    ],
    "step_05_candidate_context": [
        ("candidate_contexts", ""),                             # -> candidates -> identifiers, links
        ("materials", ""),
        ("tool_call_records", "and step_id = 'step_05_candidate_context'"),
    ],
    "step_06_developability": [
        ("liability_summaries", ""),                            # -> candidate_liabilities -> lane_results -> liability_flags
        ("tool_call_records", "and step_id = 'step_06_developability'"),
    ],
    "step_07_structure_prep": [
        ("prepared_structure_packages", ""),                   # -> prepared_structure_inputs, sequence_refs_for_prediction
    ],
    "step_08_structure_prediction": [
        ("structure_prediction_summaries", ""),                # -> candidate_structure_results -> refs / interface / plans
    ],
    "step_09_variant_design": [
        ("variant_design_summaries", ""),                      # -> step9_input_fields, step9_execution_records
    ],
}


def reset_step_projection(conn, run_id: str, step_id: str) -> None:
    for table, extra in _OWNED.get(step_id, []):
        conn.execute(
            text(f"delete from {table} where run_id = :r {extra}"),
            {"r": run_id},
        )
