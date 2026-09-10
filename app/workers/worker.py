"""Worker —— 消费队列里的 run 任务，按 workflow 驱动 Step 1→9。

控制平面是真的：真实的步骤顺序、真实的写入器派发、上一步输出喂给下一步、
Step 3 blocked 中断、失败传播、逐步审计。每步的计算部分是占位，见
workflow.py 模块头。

每个步骤在**独立事务**里执行。这样一步失败时前面已完成的步骤保留在库里，
run 置 failed 后可以从 step_records 看出走到哪一步 —— 而不是整个 run 回滚
成一片空白，无从排查。步骤写入本身是幂等的（reset_step_projection +
step_records 按 (run_id, step_id) upsert），所以重投递安全。

尚未实现：单步重试与指数退避（现在任一步失败即整个 run 置 failed）。
"""
from __future__ import annotations

from sqlalchemy import text

from ..db.audit import record as audit
from ..db.ids import new_ulid
from ..db.repositories.runs import finish_run
from ..db.repositories.steps import get_step_output
from ..db.session import make_object_store
from . import queue
from .workflow import WORKFLOW, WORKFLOW_VERSION, is_blocked


def _record_error(conn, run_id: str, step_id: str | None, exc: Exception) -> None:
    conn.execute(
        text("""insert into step_errors (step_error_id, run_id, step_id, error_type,
                  error_message, retry_count)
                values (:sid, :r, :step, 'step_failed', :msg, 0)"""),
        dict(sid=new_ulid("err"), r=run_id, step=step_id, msg=str(exc)[:500]),
    )


def process_job(engine, job: dict) -> None:
    """驱动一个 run 走完 workflow。"""
    run_id = job["payload"]["run_id"]
    store = make_object_store()

    with engine.begin() as conn:
        conn.execute(text("update runs set run_status='running', updated_at=now() "
                          "where run_id=:r"), dict(r=run_id))

    prev: dict[str, dict] = {}          # step_id -> output_payload

    for step in WORKFLOW:
        try:
            with engine.begin() as conn:            # 一步一个事务
                step.run(conn, store, run_id, prev)
                conn.execute(
                    text("update runs set current_step_id=:s, updated_at=now() "
                         "where run_id=:r"),
                    dict(s=step.step_id, r=run_id))
        except Exception as e:
            with engine.begin() as conn:
                _record_error(conn, run_id, step.step_id, e)
                finish_run(conn, run_id, "failed", actor="worker")
            return

        # 从记录之源读回，而不是直接用内存里的 payload ——
        # 确保下一步拿到的确实是落了盘的东西（JSONB 往返后的形态）
        with engine.begin() as conn:
            prev[step.step_id] = get_step_output(conn, run_id, step.step_id) or {}

        if is_blocked(step.step_id, prev[step.step_id]):
            with engine.begin() as conn:
                conn.execute(
                    text("update runs set run_status='awaiting_review', updated_at=now() "
                         "where run_id=:r"), dict(r=run_id))
                audit(conn, run_id, "run.blocked", actor="worker",
                      target_type="run", target_id=run_id,
                      payload={"blocked_at": step.step_id,
                               "reasons": prev[step.step_id].get("blocking_reasons", [])})
            return

    with engine.begin() as conn:
        finish_run(conn, run_id, "completed", actor="worker")


def worker_tick(engine) -> bool:
    """认领并处理一个任务。返回是否处理了任务。"""
    with engine.begin() as conn:
        job = queue.claim_one(conn)
    if not job:
        return False
    try:
        process_job(engine, job)
    except Exception as e:                  # workflow 之外的意外（队列/DB 故障等）
        with engine.begin() as conn:
            _record_error(conn, job["payload"].get("run_id"), None, e)
        raise
    with engine.begin() as conn:
        queue.ack(conn, job)
    return True


def run_forever(engine, interval_seconds: float = 1.0):  # pragma: no cover
    import time
    while True:
        if not worker_tick(engine):
            time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    from ..db.session import make_engine
    print(f"worker 启动，workflow={WORKFLOW_VERSION}，共 {len(WORKFLOW)} 步")
    run_forever(make_engine())
