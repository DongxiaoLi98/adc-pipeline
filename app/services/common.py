"""Small shared helpers for the service layer."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_hash(payload: dict) -> str:
    """Order-independent fingerprint of a request body.

    Same key + semantically-same body -> same hash (idempotent replay);
    same key + different body -> different hash -> 409 at the API layer.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def idem_ttl() -> timedelta:
    return timedelta(hours=int(os.getenv("IDEMPOTENCY_TTL_HOURS", "24")))


def backoff(attempt: int) -> timedelta:
    """Exponential backoff, capped at 5 minutes."""
    return timedelta(seconds=min(2 ** attempt, 300))


def max_publish_attempts() -> int:
    return int(os.getenv("OUTBOX_MAX_ATTEMPTS", "6"))
