"""ID generation contract for the ADC pipeline.

Two kinds of IDs, and the whole data model depends on keeping them straight:

1. Global IDs (ULID) — for cross-cutting registries whose rows are unique
   across all runs: ``artifacts.artifact_id``, and the surrogate ids of child
   detail rows (normalized_entities, liability_flags, audit_events, ...).
   ULIDs are lexicographically sortable by creation time, which is convenient
   for debugging and for S3 key prefixes.

2. Deterministic IDs (uuid5) — for rows that must be idempotent on rerun:
   one ``step_record`` per (run_id, step_id), one summary row per run, etc.
   Re-running a step recomputes the SAME id, so the write upserts in place
   instead of creating a duplicate.

Run-scoped natural IDs (candidate_id, material_id, file_id, tool_call_id) come
from upstream payloads and are only unique WITHIN a run. They are therefore
stored under COMPOSITE keys (run_id, <id>) in the schema — never as a bare
global primary key. See docs/01_data_model.md.
"""
from __future__ import annotations

import uuid

try:
    # ulid-py: `pip install python-ulid`
    from ulid import ULID

    def new_ulid(prefix: str | None = None) -> str:
        u = str(ULID())
        return f"{prefix}_{u}" if prefix else u

except ImportError:  # fallback so the module imports without the dependency
    def new_ulid(prefix: str | None = None) -> str:
        u = uuid.uuid4().hex
        return f"{prefix}_{u}" if prefix else u


# Stable namespace so deterministic ids are reproducible across processes/machines.
_NAMESPACE = uuid.UUID("adc1adc1-0000-4000-8000-000000000001")


def deterministic_id(prefix: str, *parts: str) -> str:
    """Reproducible id for idempotent rows.

    ``deterministic_id("steprec", run_id, step_id)`` always yields the same
    value for the same (run_id, step_id), so reruns upsert rather than insert.
    """
    key = "|".join(str(p) for p in parts)
    return f"{prefix}_{uuid.uuid5(_NAMESPACE, key)}"


def step_record_id(run_id: str, step_id: str) -> str:
    return deterministic_id("steprec", run_id, step_id)


def summary_id(prefix: str, run_id: str) -> str:
    """One-per-run summary rows (structured_queries, input_readiness, ...)."""
    return deterministic_id(prefix, run_id)
