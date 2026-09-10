"""Run submission = the transactional heart of the async backend.

One DB transaction atomically writes three rows — the run, the API idempotency
record, and the outbox event — so we never get the classic dual-write bug (DB
committed but queue never notified, or vice-versa). The outbox relay
(app/workers/outbox_relay.py) publishes the event afterwards.
"""
from __future__ import annotations

from sqlalchemy import text

from ..db.audit import record as audit
from ..db.ids import new_ulid
from .common import canonical_hash, idem_ttl, now


class Conflict(Exception):
    """Raised on idempotency-key reuse with a different body (-> HTTP 409)."""


def submit_run(conn, *, user_id: str, route: str, body: dict,
               idem_key: str | None) -> tuple[int, dict]:
    """Create a run + enqueue it. Returns (status_code, response_body).

    Idempotent: replaying the same Idempotency-Key returns the SAME response
    without creating a second run. Safe under concurrency via INSERT .. ON
    CONFLICT DO NOTHING (no read-then-write race).
    """
    req_hash = canonical_hash(body)

    if idem_key:
        hit = conn.execute(
            text("""select response_status, response_body, request_hash
                    from api_idempotency_keys
                    where user_id = :u and route = :r and key = :k"""),
            dict(u=user_id, r=route, k=idem_key),
        ).mappings().first()
        if hit:
            if hit["request_hash"] != req_hash:
                raise Conflict("Idempotency-Key reused with a different body")
            return hit["response_status"], dict(hit["response_body"])

    run_id = new_ulid("run")
    project_id = body.get("project_id")
    conn.execute(
        text("""insert into runs (run_id, project_id, user_id, run_status,
                  schema_version, pipeline_version)
                values (:run_id, :pid, :uid, 'queued', :sv, :pv)"""),
        dict(run_id=run_id, pid=project_id, uid=user_id,
             sv="adc_step_1_6_db_v0_1", pv="api_v0_1"),
    )

    resp = {"run_id": run_id, "status": "queued"}
    resp_status = 202

    if idem_key:
        # ON CONFLICT DO NOTHING makes concurrent same-key inserts safe.
        rows = conn.execute(
            text("""insert into api_idempotency_keys
                      (user_id, route, key, request_hash, run_id,
                       response_status, response_body, expires_at)
                    values (:u, :r, :k, :h, :run_id, :st, cast(:body as jsonb), :exp)
                    on conflict (user_id, route, key) do nothing"""),
            dict(u=user_id, r=route, k=idem_key, h=req_hash, run_id=run_id,
                 st=resp_status, body=_json(resp), exp=now() + idem_ttl()),
        ).rowcount
        if rows == 0:
            # A concurrent request won the race — return ITS response, drop ours.
            other = conn.execute(
                text("""select response_status, response_body
                        from api_idempotency_keys
                        where user_id = :u and route = :r and key = :k"""),
                dict(u=user_id, r=route, k=idem_key),
            ).mappings().first()
            raise _Replay(other["response_status"], dict(other["response_body"]))

    # Outbox event — same transaction as the run.
    conn.execute(
        text("""insert into outbox (event_id, topic, payload, status, next_attempt_at)
                values (:eid, 'run.submitted', cast(:p as jsonb), 'pending', now())"""),
        dict(eid=new_ulid("evt"), p=_json({"run_id": run_id})),
    )

    # Audit (immutable) — 同一事务，见 app/db/audit.py
    audit(conn, run_id, "run.submitted", actor=user_id,
          target_type="run", target_id=run_id)
    return resp_status, resp


class _Replay(Exception):
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body


def _json(d: dict) -> str:
    import json
    return json.dumps(d)
