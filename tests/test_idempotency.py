"""Idempotency contract test: running a step N times == running it once.

This is the regression test for the bug the earlier draft had (child rows
doubling on rerun). It's written against a live local Postgres (Supabase Local);
mark it as an integration test in CI.

    pytest tests/test_idempotency.py
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from app.db.session import make_engine, make_object_store
from app.db.repositories import Step2_structured_query, runs
from app.db.repositories import Step1_intake as _intake

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ, reason="needs a local Postgres"
)

SAMPLE_SQ = {
    "source_raw_request_ref": {"raw_request_record_id": "rr_1"},
    "task_intent": {"primary_intent": "existing_adc_evaluation",
                    "primary_intent_confidence": 0.9, "secondary_intents": []},
    "mentioned_entities": {"target_or_antigen_text": "HER2"},
    "normalized_entities": [
        {"original_text": "HER2", "entity_type": "target_or_antigen", "confidence": 0.9},
        {"original_text": "trastuzumab", "entity_type": "antibody", "confidence": 0.8},
    ],
}


def _count(conn, run_id):
    return conn.execute(
        text("select count(*) from normalized_entities where run_id = :r"),
        {"r": run_id},
    ).scalar()


def test_rerun_does_not_duplicate_children():
    engine = make_engine()
    with engine.connect() as conn:
        run_id = runs.create_run(conn, user_id="tester")
        conn.commit()

        # --- FK 夹具: structured_queries 外键指向 raw_requests, 先写 Step 1 ---
        _rr = f"rr_{run_id}"
        _intake.write_intake(conn, make_object_store(), run_id, {
            "raw_request_record_id": _rr, "entry_source": "test",
            "raw_user_query": "q", "intake_status": "accepted"})
        _sq = dict(SAMPLE_SQ, source_raw_request_ref={"raw_request_record_id": _rr})
        Step2_structured_query.write_structured_query(conn, run_id, _sq)
        after_first = _count(conn, run_id)

        # rerun the SAME step three more times
        for _ in range(3):
            Step2_structured_query.write_structured_query(conn, run_id, _sq)
        after_reruns = _count(conn, run_id)

        assert after_first == 2
        assert after_reruns == 2, "child rows must not accumulate on rerun"
