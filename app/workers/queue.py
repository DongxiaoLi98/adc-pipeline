"""Queue backend — the ONLY place that knows local-table vs SQS.

QUEUE_BACKEND=local  -> a Postgres `job_queue` table, claimed with
                        FOR UPDATE SKIP LOCKED (mimics SQS visibility timeout).
QUEUE_BACKEND=sqs    -> Amazon SQS (send/receive/delete).

The relay and worker call this interface only, so migrating to SQS is an env
change, not a code change — the same discipline as the object-store client.
"""
from __future__ import annotations

import json
import os

from sqlalchemy import text

from ..db.ids import new_ulid


def backend() -> str:
    return os.getenv("QUEUE_BACKEND", "local")


# ---- publish (called by the outbox relay) ---------------------------------

def publish(conn, topic: str, payload: dict) -> str:
    if backend() == "sqs":
        return _sqs_send(topic, payload)
    job_id = new_ulid("job")
    conn.execute(
        text("""insert into job_queue (job_id, topic, payload, status, visible_at)
                values (:jid, :topic, cast(:p as jsonb), 'ready', now())"""),
        dict(jid=job_id, topic=topic, p=json.dumps(payload)),
    )
    return job_id


# ---- claim / ack (called by the worker) -----------------------------------

def claim_one(conn, visibility_seconds: int = 60) -> dict | None:
    """Claim one ready job atomically. Local uses SKIP LOCKED; SQS uses receive."""
    if backend() == "sqs":
        return _sqs_receive(visibility_seconds)
    row = conn.execute(
        text("""select job_id, topic, payload from job_queue
                where status = 'ready' and visible_at <= now()
                order by visible_at
                for update skip locked
                limit 1"""),
    ).mappings().first()
    if not row:
        return None
    conn.execute(
        text("""update job_queue
                set status = 'in_progress', locked_at = now(),
                    visible_at = now() + (:vt || ' seconds')::interval,
                    attempt_count = attempt_count + 1
                where job_id = :jid"""),
        dict(jid=row["job_id"], vt=visibility_seconds),
    )
    return dict(row)


def ack(conn, job: dict) -> None:
    if backend() == "sqs":
        return _sqs_delete(job)
    conn.execute(
        text("update job_queue set status = 'done' where job_id = :jid"),
        dict(jid=job["job_id"]),
    )


# ---- SQS stubs (wired when QUEUE_BACKEND=sqs; boto3 client already in session) ----

def _sqs_send(topic: str, payload: dict) -> str:  # pragma: no cover
    import boto3
    q = os.environ["SQS_QUEUE_URL"]
    r = boto3.client("sqs").send_message(
        QueueUrl=q, MessageBody=json.dumps({"topic": topic, "payload": payload}))
    return r["MessageId"]


def _sqs_receive(vt: int) -> dict | None:  # pragma: no cover
    import boto3
    q = os.environ["SQS_QUEUE_URL"]
    r = boto3.client("sqs").receive_message(
        QueueUrl=q, MaxNumberOfMessages=1, VisibilityTimeout=vt, WaitTimeSeconds=1)
    msgs = r.get("Messages", [])
    if not msgs:
        return None
    m = msgs[0]
    body = json.loads(m["Body"])
    return {"job_id": m["ReceiptHandle"], "topic": body["topic"], "payload": body["payload"]}


def _sqs_delete(job: dict) -> None:  # pragma: no cover
    import boto3
    q = os.environ["SQS_QUEUE_URL"]
    boto3.client("sqs").delete_message(QueueUrl=q, ReceiptHandle=job["job_id"])
