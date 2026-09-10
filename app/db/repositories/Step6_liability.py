"""Step 6 — structured liability summary.

tool_call_records here is treated as the CURRENT-STEP PROJECTION: it is wiped
and rebuilt on same-run reruns (see docs/04 for the audit-vs-projection
decision). candidate_summary is intentionally not projected; if present it
survives only inside candidate_liabilities.raw_payload.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from ..tx import tx
from ..ids import new_ulid, summary_id
from ..projection import reset_step_projection
from .steps import write_step_record, upsert_tool_call

STEP_ID = "step_06_developability"


def write_liability_summary(conn, run_id: str, ls: dict) -> str:
    with tx(conn):
        srid = write_step_record(
            conn, run_id=run_id, step_id=STEP_ID, step_order=6,
            status="succeeded",   # 业务状态见 liability_summaries.prefilter_status
            input_payload={}, output_payload=ls,
            schema_version="step_06_developability_v_current",
        )
        reset_step_projection(conn, run_id, STEP_ID)

        ls_id = summary_id("liabsum", run_id)
        conn.execute(
            text(
                """
                insert into liability_summaries (liability_summary_id, run_id, step_record_id,
                  prefilter_status, tool_output_artifact_ids, notes, selection_audit, raw_payload)
                values (:ls, :run_id, :srid, :pfs, :toa, :notes, cast(:audit as jsonb), cast(:raw as jsonb))
                """
            ),
            dict(ls=ls_id, run_id=run_id, srid=srid, pfs=ls["prefilter_status"],
                 toa=ls.get("tool_output_artifacts", []), notes=ls.get("notes", []),
                 audit=json.dumps(ls.get("selection_audit", {})), raw=json.dumps(ls)),
        )

        for cl in ls.get("candidate_liabilities", []):
            cid = cl["candidate_id"]
            conn.execute(
                text(
                    """
                    insert into candidate_liabilities (run_id, candidate_id, liability_summary_id,
                      candidate_prefilter_status, candidate_overall_liability_label,
                      recommended_action, raw_payload)
                    values (:run_id, :cid, :ls, :pfs, :label, :action, cast(:raw as jsonb))
                    """
                ),
                dict(run_id=run_id, cid=cid, ls=ls_id,
                     pfs=cl["candidate_prefilter_status"],
                     label=cl["candidate_overall_liability_label"],
                     action=cl.get("recommended_action"), raw=json.dumps(cl)),
            )
            for lane in cl.get("lane_results", []):
                conn.execute(
                    text(
                        """
                        insert into lane_results (run_id, candidate_id, lane_type, run_status,
                          input_status, selected_tools, argument_mapping_audit,
                          lane_risk_category, lane_summary, raw_payload)
                        values (:run_id, :cid, :lt, :rs, :instat, :tools, cast(:ama as jsonb),
                          :risk, :summary, cast(:raw as jsonb))
                        """
                    ),
                    dict(run_id=run_id, cid=cid, lt=lane["lane_type"],
                         rs=lane["run_status"], instat=lane["input_status"],
                         tools=lane.get("selected_tools", []),
                         ama=json.dumps(lane.get("argument_mapping_audit", [])),
                         risk=lane.get("lane_risk_category", "unknown"),
                         summary=lane.get("lane_summary"), raw=json.dumps(lane)),
                )
                for flag in lane.get("liability_flags", []):
                    conn.execute(
                        text(
                            """
                            insert into liability_flags (liability_flag_id, run_id, candidate_id,
                              lane_type, flag_type, severity, risk_category, evidence_summary,
                              source_tool, source_ref, supporting_tool_call_ids, raw_flag)
                            values (:id, :run_id, :cid, :lt, :ft, :sev, :rc, :es, :st, :sr,
                              :stc, cast(:raw as jsonb))
                            """
                        ),
                        dict(id=new_ulid("flag"), run_id=run_id, cid=cid,
                             lt=lane["lane_type"], ft=flag.get("flag_type"),
                             sev=flag.get("severity"), rc=flag.get("risk_category"),
                             es=flag.get("evidence_summary"), st=flag.get("source_tool"),
                             sr=flag.get("source_ref"),
                             stc=flag.get("supporting_tool_call_ids", []),
                             raw=json.dumps(flag)),
                    )
                for tcr in lane.get("tool_call_records", []):
                    upsert_tool_call(conn, run_id, STEP_ID, tcr,
                                     candidate_id=cid, lane_type=lane["lane_type"])
    return ls_id



def get_liability_report(conn, run_id: str) -> dict:
    liabilities = conn.execute(
        text(
            """
            select c.candidate_id, c.candidate_name,
                   cl.candidate_overall_liability_label, cl.recommended_action,
                   lr.lane_type, lr.run_status as lane_run_status, lr.input_status,
                   lr.lane_risk_category, lr.lane_summary
            from candidate_liabilities cl
            join candidates c using (run_id, candidate_id)
            left join lane_results lr using (run_id, candidate_id)
            where cl.run_id = :r
            order by c.candidate_id, lr.lane_type
            """
        ),
        dict(r=run_id),
    ).mappings().all()
    flags = conn.execute(
        text("select * from liability_flags where run_id = :r order by severity desc, created_at"),
        dict(r=run_id),
    ).mappings().all()
    return {"liabilities": [dict(x) for x in liabilities],
            "flags": [dict(x) for x in flags]}
