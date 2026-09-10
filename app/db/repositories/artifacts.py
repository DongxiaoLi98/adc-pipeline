"""artifacts table repository + storage orchestration.

`artifacts` is the single pointer table used by Step 1 uploads, Step 5 material
big values, Step 6 tool outputs, and analytics exports. This module owns all
reads/writes to that table, plus the pending -> available lifecycle that
reconciles "object upload and DB write are not one transaction".

Split of concerns:
  storage.py      -> talks to the object store (put_object, presigned_url)
  artifacts.py    -> talks to the artifacts table, and orchestrates the two
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import text

from ..ids import new_ulid
from ..session import artifact_bucket, storage_backend
from .. import storage


def register_pending(conn, *, run_id, step_id, artifact_type, bucket, key, uri,
                     content_type=None, sha256=None, size_bytes=None,
                     producing_tool_call_id=None) -> str:
    artifact_id = new_ulid("art")
    conn.execute(
        text(
            """
            insert into artifacts (artifact_id, run_id, producing_step_id,
              producing_tool_call_id, artifact_type, storage_backend, bucket,
              object_key, storage_uri, upload_status, content_type, sha256, size_bytes)
            values (:aid, :run_id, :step_id, :tcid, :atype, :backend, :bucket,
              :key, :uri, 'pending', :ctype, :sha, :size)
            """
        ),
        dict(aid=artifact_id, run_id=run_id, step_id=step_id,
             tcid=producing_tool_call_id, atype=artifact_type,
             backend=storage_backend(), bucket=bucket, key=key, uri=uri,
             ctype=content_type, sha=sha256, size=size_bytes),
    )
    return artifact_id


def mark_available(conn, artifact_id: str, etag: str | None = None) -> None:
    conn.execute(
        text("update artifacts set upload_status='available', etag=:e, updated_at=now() "
             "where artifact_id=:a"),
        dict(a=artifact_id, e=etag),
    )


def mark_failed(conn, artifact_id: str) -> None:
    conn.execute(
        text("update artifacts set upload_status='failed', updated_at=now() where artifact_id=:a"),
        dict(a=artifact_id),
    )


def store_artifact(conn, store, *, run_id, step_id, artifact_type, filename, body,
                   content_type=None, producing_tool_call_id=None) -> str:
    """The full dance: register pending -> upload bytes -> mark available.

    Returns the artifact_id. Readers must only trust 'available' rows.
    """
    bucket = artifact_bucket()
    backend = storage_backend()
    key = storage.object_key(run_id, step_id, filename)
    uri = f"local://{key}" if backend == "local" else f"s3://{bucket}/{key}"
    sha = storage.sha256(body)

    artifact_id = register_pending(
        conn, run_id=run_id, step_id=step_id, artifact_type=artifact_type,
        bucket=bucket, key=key, uri=uri, content_type=content_type,
        sha256=sha, size_bytes=len(body), producing_tool_call_id=producing_tool_call_id,
    )
    try:
        etag = storage.put_object(store, bucket, key, body, content_type)
        mark_available(conn, artifact_id, etag)
    except Exception:
        mark_failed(conn, artifact_id)
        raise
    return artifact_id


def get_pointer(conn, artifact_id: str) -> dict | None:
    row = conn.execute(
        text("select artifact_id, bucket, object_key, storage_uri, upload_status "
             "from artifacts where artifact_id=:a"),
        dict(a=artifact_id),
    ).mappings().first()
    return dict(row) if row else None


def get_available_url(conn, store, artifact_id: str, ttl: int = 3600) -> str | None:
    p = get_pointer(conn, artifact_id)
    if not p or p["upload_status"] != "available":
        return None
    return storage.presigned_url(store, p["bucket"], p["object_key"], ttl)


def reap_orphans(conn, older_than_minutes: int = 60) -> int:
    """Delete artifacts stuck in 'pending' past the timeout. Run on a schedule."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=older_than_minutes)
    result = conn.execute(
        text("delete from artifacts where upload_status='pending' and created_at < :c"),
        dict(c=cutoff),
    )
    return result.rowcount or 0
