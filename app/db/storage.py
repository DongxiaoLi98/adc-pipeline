"""Object-store client ONLY. No database access lives here.

This module knows how to talk to an S3-compatible object store (Supabase
Storage / LocalStack / real AWS S3) and nothing about the `artifacts` table.
The table logic + the pending->available orchestration live in
`repositories/artifacts.py`, so the two concerns stay separate.
"""
from __future__ import annotations

import hashlib


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def object_key(run_id: str, step_id: str, filename: str) -> str:
    # Partitioned so a future Glue crawler + Athena "just works".
    return f"runs/run_id={run_id}/step={step_id}/{filename}"


def put_object(store, bucket: str, key: str, body: bytes,
               content_type: str | None = None) -> str | None:
    """Upload bytes. Returns the object's etag (None for the local demo backend)."""
    resp = store.put_object(
        Bucket=bucket, Key=key, Body=body,
        ContentType=content_type or "application/octet-stream",
    )
    return resp.get("ETag")


def presigned_url(store, bucket: str, key: str, ttl: int = 3600) -> str:
    return store.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl
    )
