"""Step 9 — structure variant & design results.

v0.3 active contract: input projection + Stage 1 (LLM tool selection) +
Stage 2 (schema mapping) + runtime execution records. Same shape family as
Step 6's selection_audit, promoted to first-class storage.

Storage discipline: step9_execution_records.tool_input_summary must be
compact/redacted — never raw structure text, sequence, A3M, API keys, full
prompts, or raw LLM responses.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from ..tx import tx
from ..ids import new_ulid, summary_id
from ..projection import reset_step_projection
from .steps import write_step_record

STEP_ID = "step_09_variant_design"


def write_variant_design(conn, run_id: str, result: dict) -> str:
    with tx(conn):
        srid = write_step_record(
            conn, run_id=run_id, step_id=STEP_ID, step_order=9,
            status="succeeded",   # 业务状态见 variant_design_summaries.runtime_execution_mode
            input_payload={}, output_payload=result,
            schema_version="step_09_variant_design_v_current",
        )
        reset_step_projection(conn, run_id, STEP_ID)

        sum_id = summary_id("vdsum", run_id)
        conn.execute(
            text(
                """
                insert into variant_design_summaries (variant_design_summary_id, run_id,
                  step_record_id, input_projection_summary, projection_missing_inputs,
                  stage1_selection_source, stage1_selected_tools, stage1_rejected_tools,
                  stage2_mapped_tools, stage2_uninvokable_tools, stage2_argument_mapping_audit,
                  runtime_execution_mode, runtime_executed_tools, raw_payload)
                values (:sid, :run_id, :srid, cast(:ips as jsonb), :pmi,
                  :s1src, cast(:s1sel as jsonb), cast(:s1rej as jsonb),
                  cast(:s2map as jsonb), :s2unin, cast(:s2audit as jsonb),
                  :rmode, :rexec, cast(:raw as jsonb))
                """
            ),
            dict(sid=sum_id, run_id=run_id, srid=srid,
                 ips=json.dumps(result.get("step9_input_projection_summary", {})),
                 pmi=result.get("step9_projection_missing_inputs", []),
                 s1src=result.get("step9_stage1_selection_source"),
                 s1sel=json.dumps(result.get("step9_stage1_selected_tools", [])),
                 s1rej=json.dumps(result.get("step9_stage1_rejected_tools_with_reason", [])),
                 s2map=json.dumps(result.get("step9_stage2_mapped_tools", [])),
                 s2unin=result.get("step9_stage2_uninvokable_tools", []),
                 s2audit=json.dumps(result.get("step9_stage2_argument_mapping_audit", [])),
                 rmode=result.get("step9_runtime_execution_mode"),
                 rexec=result.get("step9_runtime_executed_tools", []),
                 raw=json.dumps(result)),
        )

        for f in result.get("step9_input_fields", []):
            conn.execute(
                text(
                    """
                    insert into step9_input_fields (step9_input_field_id, run_id,
                      variant_design_summary_id, field_ref, candidate_id, source_step,
                      source_artifact, source_path, field_name, field_type, value_kind,
                      semantic_role, chain_role, supports_tool_args, can_resolve_at_runtime,
                      status, missing_reason, llm_safe_metadata, raw_payload)
                    values (:id, :run_id, :sid, :fref, :cid, :sstep, :sart, :spath,
                      :fname, :ftype, :vkind, :srole, :crole, :sargs, :canres,
                      :status, :mreason, cast(:llm as jsonb), cast(:raw as jsonb))
                    """
                ),
                dict(id=new_ulid("s9field"), run_id=run_id, sid=sum_id,
                     fref=f["field_ref"], cid=f.get("candidate_id"),
                     sstep=f.get("source_step"), sart=f.get("source_artifact"),
                     spath=f.get("source_path"), fname=f.get("field_name"),
                     ftype=f.get("field_type"), vkind=f.get("value_kind"),
                     srole=f.get("semantic_role"), crole=f.get("chain_role"),
                     sargs=f.get("supports_tool_args", []),
                     canres=bool(f.get("can_resolve_at_runtime", False)),
                     status=f.get("status"), mreason=f.get("missing_reason"),
                     llm=json.dumps(f.get("llm_safe_metadata", {})),
                     raw=json.dumps(f)),
            )

        for rec in result.get("step9_runtime_execution_records", []):
            conn.execute(
                text(
                    """
                    insert into step9_execution_records (step9_execution_record_id, run_id,
                      variant_design_summary_id, tool_name, candidate_id, run_status,
                      started_at, finished_at, tool_input_summary, tool_output_artifact_id,
                      tool_output_ref, error_message, raw_payload)
                    values (:id, :run_id, :sid, :tool, :cid, :status, :started, :finished,
                      cast(:inp as jsonb), :art, :ref, :err, cast(:raw as jsonb))
                    """
                ),
                dict(id=new_ulid("s9exec"), run_id=run_id, sid=sum_id,
                     tool=rec["tool_name"], cid=rec.get("candidate_id"),
                     status=rec.get("run_status", "not_run"),
                     started=rec.get("started_at"), finished=rec.get("finished_at"),
                     inp=json.dumps(rec.get("tool_input_summary", {})),
                     art=rec.get("tool_output_artifact_id"),
                     ref=rec.get("tool_output_ref"), err=rec.get("error_message"),
                     raw=json.dumps(rec)),
            )
    return sum_id
