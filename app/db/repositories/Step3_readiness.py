"""Step 3 — input readiness, missing-input checklist, file checks.

Note: ``can_continue`` is computed here in the app layer (severity != 'blocking')
and written as a plain boolean — it is NOT a generated column, so changing the
severity vocabulary later needs no DDL migration.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from ..tx import tx
from ..ids import new_ulid, summary_id
from ..projection import reset_step_projection
from .steps import write_step_record

STEP_ID = "step_03_input_readiness"

# The current code emits ten FLAT evidence fields (not a nested "evidence" object).
# We collect them into the evidence jsonb column.
_EVIDENCE_FIELDS = (
    "target_evidence", "antibody_evidence", "payload_evidence", "linker_evidence",
    "structure_or_sequence_evidence", "constraints_evidence", "adc_task_intent_evidence",
    "structure_input_evidence", "sequence_input_evidence", "candidate_file_evidence",
)


def _collect_evidence(readiness: dict) -> dict:
    # Prefer the flat fields; fall back to a nested "evidence" object if present.
    flat = {k: readiness[k] for k in _EVIDENCE_FIELDS if k in readiness}
    return flat or readiness.get("evidence", {})


def write_readiness(conn, run_id: str, readiness: dict) -> str:
    with tx(conn):
        status = "blocked" if readiness.get("blocking_reasons") else "succeeded"
        srid = write_step_record(
            conn, run_id=run_id, step_id=STEP_ID, step_order=3, status=status,
            input_payload={}, output_payload=readiness,
            schema_version="step_03_input_readiness_v_current",
        )
        reset_step_projection(conn, run_id, STEP_ID)

        ir_id = summary_id("ready", run_id)
        p = readiness["basic_adc_input_presence"]
        conn.execute(
            text(
                """
                insert into input_readiness (input_readiness_id, run_id, step_record_id,
                  target_or_antigen_present, antibody_candidate_present, payload_present,
                  linker_present, structure_or_sequence_present, constraints_present,
                  adc_task_intent_present, structure_input_present, sequence_input_present,
                  candidate_file_present, evidence, blocking_reasons, readiness_status, raw_payload)
                values (:ir, :run_id, :srid,
                  :toap, :acp, :pp, :lp, :sosp, :cp, :atip, :sip, :seqp, :cfp,
                  cast(:ev as jsonb), :br, :rs, cast(:raw as jsonb))
                """
            ),
            dict(ir=ir_id, run_id=run_id, srid=srid,
                 toap=p["target_or_antigen_present"], acp=p["antibody_candidate_present"],
                 pp=p["payload_present"], lp=p["linker_present"],
                 sosp=p["structure_or_sequence_present"], cp=p["constraints_present"],
                 atip=p["adc_task_intent_present"], sip=p["structure_input_present"],
                 seqp=p["sequence_input_present"], cfp=p["candidate_file_present"],
                 ev=json.dumps(_collect_evidence(readiness)),
                 br=readiness.get("blocking_reasons", []),
                 rs=readiness.get("readiness_status", "unknown"),
                 raw=json.dumps(readiness)),
        )

        for item in readiness.get("missing_input_checklist", []):
            conn.execute(
                text(
                    """
                    insert into missing_input_items (missing_input_item_id, run_id,
                      input_readiness_id, field, severity, message, category,
                      evidence_field, can_continue)
                    values (:id, :run_id, :ir, :field, :sev, :msg, :cat, :ef, :cc)
                    """
                ),
                dict(id=new_ulid("miss"), run_id=run_id, ir=ir_id,
                     field=item["field"], sev=item["severity"], msg=item["message"],
                     cat=item.get("category"), ef=item.get("evidence_field"),
                     cc=item["severity"] != "blocking"),  # app-layer computation
            )

        # uploaded_file_checks: upstream key is `exists` -> column `file_exists`
        for chk in readiness.get("uploaded_file_checks", []):
            conn.execute(
                text(
                    """
                    insert into uploaded_file_checks (uploaded_file_check_id, run_id,
                      input_readiness_id, file_id, file_exists, checksum_ok, format_ok,
                      inferred_role, storage_path_present, content_type_present,
                      size_bytes_present, notes)
                    values (:id, :run_id, :ir, :fid, :fex, :cok, :fok, :role,
                      :sp, :ct, :sz, :notes)
                    """
                ),
                dict(id=new_ulid("filechk"), run_id=run_id, ir=ir_id,
                     fid=chk.get("file_id"),
                     fex=chk.get("exists"),                 # upstream `exists` -> file_exists
                     cok=chk.get("checksum_ok"), fok=chk.get("format_ok"),
                     role=chk.get("inferred_role"),
                     sp=chk.get("storage_path_present"),
                     ct=chk.get("content_type_present"),
                     sz=chk.get("size_bytes_present"), notes=chk.get("notes")),
            )
    return ir_id


def get_missing_inputs(conn, run_id: str) -> list[dict]:
    rows = conn.execute(
        text(
            "select field, severity, message, category, can_continue "
            "from missing_input_items where run_id = :r order by severity"
        ),
        dict(r=run_id),
    ).mappings().all()
    return [dict(r) for r in rows]
