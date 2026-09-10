"""ID contract unit tests — pure, no database needed.

Checks the two guarantees the whole data model relies on:
  * deterministic ids are stable (same input -> same id) so reruns upsert
  * ULIDs are unique (no collisions across rows)
"""
from app.db.ids import deterministic_id, step_record_id, summary_id, new_ulid


def test_deterministic_id_is_stable():
    a = step_record_id("run_1", "step_02_structured_query")
    b = step_record_id("run_1", "step_02_structured_query")
    assert a == b  # rerun computes the same id -> upsert in place


def test_deterministic_id_varies_by_input():
    assert step_record_id("run_1", "step_02") != step_record_id("run_2", "step_02")
    assert summary_id("sq", "run_1") != summary_id("sq", "run_2")


def test_ulid_is_unique():
    ids = {new_ulid("art") for _ in range(10_000)}
    assert len(ids) == 10_000  # no collisions
