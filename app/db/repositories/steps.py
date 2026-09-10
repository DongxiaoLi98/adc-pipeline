"""Generic step-record writer + reader, shared by every step repository."""
from __future__ import annotations

import json

from sqlalchemy import text

from ..audit import record as audit
from ..ids import step_record_id

# 冻结的执行状态词汇（见 migration 0006）。业务状态属于各自的投影表，不进这一列。
STEP_STATUSES = ("running", "succeeded", "failed", "blocked")


def write_step_record(
    conn,
    *,
    run_id: str,
    step_id: str,
    step_order: int,
    status: str,
    input_payload: dict,
    output_payload: dict,
    schema_version: str,
    source_step_record_ids: list[str] | None = None,
    model_version: str | None = None,
    code_commit_sha: str | None = None,
    actor: str = "system",
) -> str:
    if status not in STEP_STATUSES:
        raise ValueError(
            f"step_status={status!r} 不在允许的取值内 {STEP_STATUSES}。"
            "业务状态请写入该步骤自己的投影表列，不要塞进 step_status。")
    srid = step_record_id(run_id, step_id)  # deterministic -> upsert in place on rerun
    conn.execute(
        text(
            """
            insert into step_records (
              step_record_id, run_id, step_id, step_order, step_status,
              source_step_record_ids, input_payload, output_payload,
              schema_version, model_version, code_commit_sha, started_at, finished_at)
            values (
              :srid, :run_id, :step_id, :order, :status,
              :srcs, cast(:inp as jsonb), cast(:outp as jsonb),
              :sv, :mv, :sha, now(), now())
            on conflict (run_id, step_id) do update set
              step_status    = excluded.step_status,
              input_payload  = excluded.input_payload,
              output_payload = excluded.output_payload,
              schema_version = excluded.schema_version,
              model_version  = excluded.model_version,
              code_commit_sha= excluded.code_commit_sha,
              finished_at    = now(),
              updated_at     = now()
            """
        ),
        dict(srid=srid, run_id=run_id, step_id=step_id, order=step_order, status=status,
             srcs=source_step_record_ids or [], inp=json.dumps(input_payload),
             outp=json.dumps(output_payload), sv=schema_version,
             mv=model_version, sha=code_commit_sha),
    )
    conn.execute(
        text(
            """
            update runs set current_step_id = :step_id,
              run_status = case when :status = 'failed' then 'failed' else 'running' end,
              updated_at = now()
            where run_id = :run_id
            """
        ),
        dict(run_id=run_id, step_id=step_id, status=status),
    )
    audit(conn, run_id, "step.written", actor=actor,
          target_type="step", target_id=step_id,
          payload={"step_status": status, "step_order": step_order})
    return srid


def get_step_output(conn, run_id: str, step_id: str) -> dict | None:
    row = conn.execute(
        text("select output_payload from step_records where run_id = :r and step_id = :s"),
        dict(r=run_id, s=step_id),
    ).mappings().first()
    return dict(row["output_payload"]) if row else None


def upsert_tool_call(conn, run_id, step_id, tcr: dict,
                     candidate_id=None, lane_type=None) -> None:
    """Shared ToolCallRecord writer (used by Step 5 and Step 6).

    Step 6 passes candidate_id + lane_type (tool calls sit inside a lane);
    Step 5 tool calls are top-level, so both are None.
    """
    conn.execute(
        text(
            """
            insert into tool_call_records (run_id, tool_call_id, step_id, candidate_id,
              lane_type, tool_name, agent_name, run_status, started_at, finished_at,
              idempotency_key, tool_input_summary, tool_output_artifact_id,
              tool_output_ref, error_message)
            values (:run_id, :tcid, :step_id, :cid, :lt, :tool, :agent, :status,
              :started, :finished, :idem, cast(:inp as jsonb), :art, :ref, :err)
            on conflict (run_id, tool_call_id) do update set
              run_status = excluded.run_status,
              finished_at = excluded.finished_at,
              tool_output_artifact_id = excluded.tool_output_artifact_id,
              tool_output_ref = excluded.tool_output_ref,
              error_message = excluded.error_message
            """
        ),
        dict(run_id=run_id, tcid=tcr["tool_call_id"], step_id=step_id,
             cid=candidate_id, lt=lane_type, tool=tcr["tool_name"],
             agent=tcr.get("agent_name"), status=tcr["run_status"],
             started=tcr.get("started_at"), finished=tcr.get("finished_at"),
             idem=tcr.get("idempotency_key"),
             inp=json.dumps(tcr.get("tool_input_summary", {})),
             art=tcr.get("tool_output_artifact_id"), ref=tcr.get("tool_output_ref"),
             err=tcr.get("error_message")),
    )
