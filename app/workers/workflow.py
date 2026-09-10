"""流水线 workflow 定义 —— 步骤顺序、依赖、以及每步调用哪个写入器。

设计边界（重要）：
本模块把**控制平面**做真 —— 真实的步骤顺序、真实的写入器派发、上一步输出
喂给下一步、blocked 中断、失败传播。每个步骤的**计算部分**仍是占位：
`_placeholder_*` 函数造一个符合 schema 的输出，算法团队后续只需替换这些函数，
worker 和 workflow 结构不用动。

三条已定的语义：
  1. **Step 4 留空位**。step_order 是 1,2,3,5,6,7,8,9 —— 不重新编号，
     否则历史数据的 step_order 会失去意义。Step 4 将来落地时插回 4 即可。
  2. **Step 3 判定 blocked 就停**。readiness 有 blocking_reasons 时，run 停在
     awaiting_review 等人工介入，不继续跑 Step 5。这是 schema 里已有的状态。
  3. **步骤间只通过 step_records.output_payload 传数据**。上一步的完整输出是
     记录之源（ADR-3），下一步从那里读，不去查投影表 —— 投影是派生的、可重建的。

尚未实现（明确留给下一轮）：
  * 单步重试与指数退避：现在任一步失败即整个 run 置 failed
  * Step 6 之后的人工复核门（POST /v1/runs/{id}/reviews 端点还没实现）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..db.repositories import (
    Step1_intake as intake,
    Step2_structured_query as structured_query,
    Step3_readiness as readiness,
    Step5_candidate_context as candidate_context,
    Step6_liability as liability,
    Step7_structure_prep as structure_prep,
    Step8_structure_prediction as structure_prediction,
    Step9_variant_design as variant_design,
)

WORKFLOW_VERSION = "workflow_v0_1"


@dataclass(frozen=True)
class Step:
    step_id: str
    order: int
    run: Callable          # (conn, store, run_id, prev) -> 写入的 output_payload


# ---------------------------------------------------------------------------
# 占位计算：算法团队替换这些函数即可，其余结构不动。
# 每个函数接收 prev（{step_id: output_payload}），返回该步骤的输出。
# ---------------------------------------------------------------------------

def _step1(conn, store, run_id, prev):
    payload = {
        "raw_request_record_id": "rr_1",
        "entry_source": "api",
        "raw_user_query": "(placeholder) evaluate an ADC candidate",
        "intake_status": "accepted",
        "uploaded_files": [],
    }
    intake.write_intake(conn, store, run_id, payload)
    return payload


def _step2(conn, store, run_id, prev):
    rr = prev["step_01_intake"]["raw_request_record_id"]     # 上一步的输出
    payload = {
        "source_raw_request_ref": {"raw_request_record_id": rr},
        "task_intent": {"primary_intent": "existing_adc_evaluation",
                        "primary_intent_confidence": 0.9, "secondary_intents": []},
        "mentioned_entities": {"target_or_antigen_text": "HER2"},
        "normalized_entities": [
            {"original_text": "HER2", "entity_type": "target_or_antigen", "confidence": 0.9},
        ],
    }
    structured_query.write_structured_query(conn, run_id, payload)
    return payload


def _step3(conn, store, run_id, prev):
    entities = prev["step_02_structured_query"]["normalized_entities"]
    has_target = any(e["entity_type"] == "target_or_antigen" for e in entities)
    payload = {
        "readiness_status": "ready" if has_target else "blocked",
        # 非空即中断后续步骤 —— 见模块头注释第 2 条
        "blocking_reasons": [] if has_target else ["缺少 target_or_antigen"],
        "basic_adc_input_presence": {
            "target_or_antigen_present": has_target, "antibody_candidate_present": True,
            "payload_present": False, "linker_present": False,
            "structure_or_sequence_present": False, "constraints_present": False,
            "adc_task_intent_present": True, "structure_input_present": False,
            "sequence_input_present": False, "candidate_file_present": False,
        },
        "missing_input_checklist": [],
    }
    readiness.write_readiness(conn, run_id, payload)
    return payload


def _step5(conn, store, run_id, prev):
    payload = {
        "context_build_status": "completed",
        "candidates": [{"candidate_id": "cand_001", "candidate_role": "primary",
                        "identifiers": [{"id_type": "chembl", "id_value": "CHEMBL1"}],
                        "adc_links": {"target_material_ids": ["mat_001"]}}],
        "materials": [{"material_id": "mat_001", "material_type": "target",
                       "value_inline": "HER2"}],
    }
    candidate_context.write_candidate_context(conn, store, run_id, payload)
    return payload


def _step6(conn, store, run_id, prev):
    cands = [c["candidate_id"] for c in prev["step_05_candidate_context"]["candidates"]]
    payload = {
        "prefilter_status": "completed",
        "candidate_liabilities": [{
            "candidate_id": cid,
            "candidate_prefilter_status": "completed",
            "candidate_overall_liability_label": "low",
            "lane_results": [{"lane_type": "aggregation", "run_status": "succeeded",
                              "input_status": "complete", "lane_risk_category": "low",
                              "liability_flags": []}],
        } for cid in cands],
    }
    liability.write_liability_summary(conn, run_id, payload)
    return payload


def _step7(conn, store, run_id, prev):
    cands = [c["candidate_id"] for c in prev["step_05_candidate_context"]["candidates"]]
    payload = {
        "package_status": "prepared",
        "prepared_structure_inputs": [{"candidate_id": c, "input_status": "ok"} for c in cands],
        "sequence_refs_for_prediction": [
            {"candidate_id": c, "chain_role": "heavy",
             "sequence_length": 120, "sha256_prefix": "placeholder"} for c in cands],
    }
    structure_prep.write_structure_prep(conn, run_id, payload)
    return payload


def _step8(conn, store, run_id, prev):
    cands = [i["candidate_id"] for i in prev["step_07_structure_prep"]["prepared_structure_inputs"]]
    payload = {
        "summary_status": "succeeded",
        "candidate_structure_results": [{
            "candidate_id": c, "has_complex_structure": True,
            "has_validated_structure": False, "downstream_handoff": {"ok": True},
            "complex_structure_refs": [], "interface_analysis_records": [],
            "complex_prediction_plans": [],
        } for c in cands],
    }
    structure_prediction.write_structure_prediction(conn, run_id, payload)
    return payload


def _step9(conn, store, run_id, prev):
    payload = {
        "stage1_status": "succeeded",
        "runtime_execution_mode": "not_run",
        "step9_input_fields": [],
        "step9_runtime_execution_records": [],
    }
    variant_design.write_variant_design(conn, run_id, payload)
    return payload


# step_order 跳过 4 —— Step 4 尚未落地，编号留空位
WORKFLOW: tuple[Step, ...] = (
    Step("step_01_intake",               1, _step1),
    Step("step_02_structured_query",     2, _step2),
    Step("step_03_input_readiness",      3, _step3),
    Step("step_05_candidate_context",    5, _step5),
    Step("step_06_developability",       6, _step6),
    Step("step_07_structure_prep",       7, _step7),
    Step("step_08_structure_prediction", 8, _step8),
    Step("step_09_variant_design",       9, _step9),
)


def is_blocked(step_id: str, payload: dict) -> bool:
    """Step 3 判定输入不足时中断后续步骤。"""
    return step_id == "step_03_input_readiness" and bool(payload.get("blocking_reasons"))
