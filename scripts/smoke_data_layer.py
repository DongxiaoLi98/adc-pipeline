"""End-to-end smoke test for the data layer: Step 1 -> 2 -> 5 -> 6 -> 7 -> 8 -> 9,
each written twice to prove reset_step_projection() makes reruns idempotent.

    export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres
    export ARTIFACT_STORAGE_BACKEND=local
    python scripts/smoke_data_layer.py
"""
from __future__ import annotations

from sqlalchemy import text

from app.db.session import make_engine, make_object_store
from app.db.repositories import (
    runs, Step1_intake as intake, Step2_structured_query as structured_query,
    Step3_readiness as readiness, Step5_candidate_context as candidate_context,
    Step6_liability as liability, Step7_structure_prep as structure_prep,
    Step8_structure_prediction as structure_prediction,
    Step9_variant_design as variant_design,
)

RR = {
    "raw_request_record_id": "rr_1",
    "entry_source": "web_form",
    "submitted_by": "tester",
    "raw_user_query": "Evaluate a HER2 ADC",
    "intake_status": "accepted",
    "uploaded_files": [],
}

SQ = {
    "source_raw_request_ref": {"raw_request_record_id": "rr_1"},
    "task_intent": {"primary_intent": "existing_adc_evaluation",
                    "primary_intent_confidence": 0.9, "secondary_intents": []},
    "mentioned_entities": {"target_or_antigen_text": "HER2"},
    "normalized_entities": [
        {"original_text": "HER2", "entity_type": "target_or_antigen", "confidence": 0.9},
        {"original_text": "trastuzumab", "entity_type": "antibody", "confidence": 0.8},
    ],
}

RD = {
    "readiness_status": "ready",
    "blocking_reasons": [],
    "basic_adc_input_presence": {
        "target_or_antigen_present": True, "antibody_candidate_present": True,
        "payload_present": False, "linker_present": False,
        "structure_or_sequence_present": False, "constraints_present": False,
        "adc_task_intent_present": True, "structure_input_present": False,
        "sequence_input_present": False, "candidate_file_present": False,
    },
    "target_evidence": "from query",
    "missing_input_checklist": [
        {"field": "payload", "severity": "warning", "message": "no payload given"}
    ],
}

CTX = {
    "context_build_status": "completed",
    "candidates": [{
        "candidate_id": "cand_001",
        "candidate_role": "primary",
        "identifiers": [{"id_type": "chembl", "id_value": "CHEMBL1"}],
        "adc_links": {"target_material_ids": ["mat_001"]},
    }],
    "materials": [{"material_id": "mat_001", "material_type": "target",
                   "value_inline": "HER2"}],
}

LS = {
    "prefilter_status": "completed",
    "candidate_liabilities": [{
        "candidate_id": "cand_001",
        "candidate_prefilter_status": "completed",
        "candidate_overall_liability_label": "low",
        "lane_results": [{
            "lane_type": "aggregation", "run_status": "succeeded",
            "input_status": "complete", "lane_risk_category": "low",
            "liability_flags": [{"flag_type": "patch", "severity": "low"}],
        }],
    }],
}

PKG = {
    "package_status": "prepared",
    "prepared_structure_inputs": [{"candidate_id": "cand_001", "input_status": "ok"}],
    "sequence_refs_for_prediction": [{"candidate_id": "cand_001", "chain_role": "heavy",
                                      "sequence_length": 120, "sha256_prefix": "abc123"}],
}

PRED = {
    "summary_status": "succeeded",
    "candidate_structure_results": [{
        "candidate_id": "cand_001",
        "has_complex_structure": True,
        "has_validated_structure": False,
        "downstream_handoff": {"ok": True},
        "complex_structure_refs": [{"ref_kind": "predicted", "pdb_id": None}],
        "interface_analysis_records": [{"record_kind": "interface", "metrics": {"sasa": 1.0}}],
        "complex_prediction_plans": [{"plan_status": "planned", "plan_detail": {"tool": "af3"}}],
    }],
}

VAR = {
    "stage1_status": "succeeded",
    "step9_input_fields": [{"field_ref": "cand_001.heavy_chain", "field_name": "seq",
                            "status": "present"}],
    "step9_runtime_execution_records": [{"tool_name": "designer", "run_status": "succeeded"}],
}


def count(conn, table, run_id):
    return conn.execute(text(f"select count(*) from {table} where run_id=:r"),
                        {"r": run_id}).scalar()


def main():
    engine, store = make_engine(), make_object_store()
    with engine.begin() as conn:
        run_id = runs.create_run(conn, user_id="smoke")

    # 0005 之后 raw_requests 是 (run_id, raw_request_record_id) 复合主键，
    # 所以固定的上游 id "rr_1" 可以在每个 run 里重复使用。
    rr, sq = RR, SQ

    writers = [
        ("step_01_intake", lambda c: intake.write_intake(c, store, run_id, rr)),
        ("step_02_structured_query", lambda c: structured_query.write_structured_query(c, run_id, sq)),
        ("step_03_input_readiness", lambda c: readiness.write_readiness(c, run_id, RD)),
        ("step_05_candidate_context", lambda c: candidate_context.write_candidate_context(c, store, run_id, CTX)),
        ("step_06_developability", lambda c: liability.write_liability_summary(c, run_id, LS)),
        ("step_07_structure_prep", lambda c: structure_prep.write_structure_prep(c, run_id, PKG)),
        ("step_08_structure_prediction", lambda c: structure_prediction.write_structure_prediction(c, run_id, PRED)),
        ("step_09_variant_design", lambda c: variant_design.write_variant_design(c, run_id, VAR)),
    ]

    probes = {
        "step_02_structured_query": "normalized_entities",
        "step_05_candidate_context": "candidates",
        "step_06_developability": "lane_results",
        "step_07_structure_prep": "sequence_refs_for_prediction",
        "step_08_structure_prediction": "candidate_structure_results",
        "step_09_variant_design": "step9_execution_records",
    }

    ok = True
    for step_id, fn in writers:
        with engine.begin() as conn:
            fn(conn)                       # first write
        with engine.begin() as conn:
            fn(conn)                       # rerun -> must not duplicate
            fn(conn)                       # and again
        probe = probes.get(step_id)
        with engine.begin() as conn:
            n = count(conn, probe, run_id) if probe else "-"
        flag = "OK " if (probe is None or n in (1, 2)) else "!! "
        if flag.startswith("!!"):
            ok = False
        print(f"{flag}{step_id:<30} {probe or '':<32} rows after 3 writes = {n}")

    with engine.begin() as conn:
        runs.finish_run(conn, run_id, "completed")      # 绕过 worker 的路径必须显式收尾
        steps = count(conn, "step_records", run_id)
        audits = count(conn, "audit_events", run_id)
        final = conn.execute(text("select run_status from runs where run_id=:r"),
                             {"r": run_id}).scalar()
    print(f"run_status={final}")
    print(f"\nrun_id={run_id}  step_records={steps}  audit_events={audits}")
    passed = ok and steps == len(writers)
    print("SMOKE:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
