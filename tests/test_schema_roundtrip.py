"""Roundtrip test: a step payload survives JSONB storage and comes back intact.

This guards the document-of-record guarantee (ADR-3): whatever the model shape,
output_payload must return byte-for-value equal. Integration test (needs DB).
"""
import os
import pytest

from app.db.session import make_engine, make_object_store
from app.db.repositories import Step2_structured_query, runs
from app.db.repositories.steps import get_step_output
from app.db.repositories import Step1_intake as _intake

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ,
                                reason="needs a local Postgres")

SAMPLE = {
    "source_raw_request_ref": {"raw_request_record_id": "rr_1"},
    "task_intent": {"primary_intent": "existing_adc_evaluation", "secondary_intents": []},
    "mentioned_entities": {"target_or_antigen_text": "HER2"},
    "normalized_entities": [{"original_text": "HER2", "entity_type": "target_or_antigen"}],
    "nested": {"deep": {"list": [1, 2, {"k": "v"}]}},  # arbitrary shape must survive
}


def test_payload_roundtrips_through_jsonb():
    engine = make_engine()
    with engine.begin() as conn:
        run_id = runs.create_run(conn, user_id="tester")
        # --- FK 夹具: structured_queries 外键指向 raw_requests, 先写 Step 1 ---
        _rr = f"rr_{run_id}"
        _intake.write_intake(conn, make_object_store(), run_id, {
            "raw_request_record_id": _rr, "entry_source": "test",
            "raw_user_query": "q", "intake_status": "accepted"})
        _sq = dict(SAMPLE, source_raw_request_ref={"raw_request_record_id": _rr})
        Step2_structured_query.write_structured_query(conn, run_id, _sq)
        out = get_step_output(conn, run_id, "step_02_structured_query")
        assert out["nested"] == SAMPLE["nested"]
        assert out["task_intent"]["primary_intent"] == "existing_adc_evaluation"
