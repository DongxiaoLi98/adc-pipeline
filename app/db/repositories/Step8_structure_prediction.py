"""Step 8 — structure prediction & interface results.

Standard writer pattern. `downstream_handoff` is kept whole in JSONB; the two
most-filtered booleans (has_complex_structure / has_validated_structure) are
projected as columns for indexing (index candidates in the v0.3 diff).

Business rule: an uploaded/locally-validated structure alone must NOT set
has_complex_structure=true — that flag means a real complex structure exists.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from ..tx import tx
from ..ids import new_ulid, summary_id
from ..projection import reset_step_projection
from .steps import write_step_record

STEP_ID = "step_08_structure_prediction"


def write_structure_prediction(conn, run_id: str, result: dict) -> str:
    with tx(conn):
        srid = write_step_record(
            conn, run_id=run_id, step_id=STEP_ID, step_order=8,
            status="succeeded",   # 业务状态见 structure_prediction_summaries.summary_status
            input_payload={}, output_payload=result,
            schema_version="step_08_structure_prediction_v_current",
        )
        reset_step_projection(conn, run_id, STEP_ID)

        sum_id = summary_id("structsum", run_id)
        conn.execute(
            text(
                """
                insert into structure_prediction_summaries (structure_prediction_summary_id,
                  run_id, step_record_id, summary_status, notes, raw_payload)
                values (:sid, :run_id, :srid, :status, :notes, cast(:raw as jsonb))
                """
            ),
            dict(sid=sum_id, run_id=run_id, srid=srid,
                 status=result.get("summary_status", "completed"),
                 notes=result.get("notes", []), raw=json.dumps(result)),
        )

        for cr in result.get("candidate_structure_results", []):
            cid = cr["candidate_id"]
            handoff = cr.get("downstream_handoff", {})
            conn.execute(
                text(
                    """
                    insert into candidate_structure_results (run_id, candidate_id,
                      structure_prediction_summary_id, complex_prediction_input_status,
                      prediction_runtime_status, has_complex_structure, has_validated_structure,
                      downstream_handoff, missing_prediction_inputs,
                      prediction_tool_contract_notes, raw_payload)
                    values (:run_id, :cid, :sid, :cin, :prs, :hcs, :hvs,
                      cast(:handoff as jsonb), :missing, :notes, cast(:raw as jsonb))
                    """
                ),
                dict(run_id=run_id, cid=cid, sid=sum_id,
                     cin=cr.get("complex_prediction_input_status"),
                     prs=cr.get("prediction_runtime_status"),
                     hcs=bool(handoff.get("has_complex_structure", False)),
                     hvs=bool(handoff.get("has_validated_structure", False)),
                     handoff=json.dumps(handoff),
                     missing=cr.get("missing_prediction_inputs", []),
                     notes=cr.get("prediction_tool_contract_notes", []),
                     raw=json.dumps(cr)),
            )

            for ref in cr.get("complex_structure_refs", []):
                conn.execute(
                    text(
                        """
                        insert into complex_structure_refs (complex_structure_ref_id, run_id,
                          candidate_id, ref_kind, pdb_id, artifact_id, source_ref, raw_payload)
                        values (:id, :run_id, :cid, :kind, :pdb, :art, :sref, cast(:raw as jsonb))
                        """
                    ),
                    dict(id=new_ulid("cxref"), run_id=run_id, cid=cid,
                         kind=ref.get("ref_kind"), pdb=ref.get("pdb_id"),
                         art=ref.get("artifact_id"), sref=ref.get("source_ref"),
                         raw=json.dumps(ref)),
                )

            for rec in cr.get("interface_analysis_records", []):
                conn.execute(
                    text(
                        """
                        insert into interface_analysis_records (interface_analysis_record_id,
                          run_id, candidate_id, record_kind, metrics, source_tool,
                          source_ref, raw_payload)
                        values (:id, :run_id, :cid, :kind, cast(:metrics as jsonb), :tool, :sref, cast(:raw as jsonb))
                        """
                    ),
                    dict(id=new_ulid("iface"), run_id=run_id, cid=cid,
                         kind=rec.get("record_kind"),
                         metrics=json.dumps(rec.get("metrics", {})),
                         tool=rec.get("source_tool"), sref=rec.get("source_ref"),
                         raw=json.dumps(rec)),
                )

            for plan in cr.get("complex_prediction_plans", []):
                conn.execute(
                    text(
                        """
                        insert into complex_prediction_plans (complex_prediction_plan_id, run_id,
                          candidate_id, plan_status, plan_detail, raw_payload)
                        values (:id, :run_id, :cid, :status, cast(:detail as jsonb), cast(:raw as jsonb))
                        """
                    ),
                    dict(id=new_ulid("plan"), run_id=run_id, cid=cid,
                         status=plan.get("plan_status"),
                         detail=json.dumps(plan.get("plan_detail", {})),
                         raw=json.dumps(plan)),
                )
    return sum_id
