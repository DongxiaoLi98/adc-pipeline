"""Run lifecycle."""
from __future__ import annotations

from sqlalchemy import text

from ..audit import record as audit
from ..ids import new_ulid

# runs.run_status 的终态。非终态：created / queued / running / awaiting_review
TERMINAL_RUN_STATUSES = ("completed", "failed")


def create_run(conn, user_id: str, project_id: str | None = None) -> str:
    run_id = new_ulid("run")
    conn.execute(
        text(
            """
            insert into runs (run_id, project_id, user_id, run_status, current_step_id,
              schema_version, pipeline_version)
            values (:run_id, :project_id, :user_id, 'created', null,
              :sv, :pv)
            on conflict (run_id) do nothing
            """
        ),
        dict(run_id=run_id, project_id=project_id, user_id=user_id,
             sv="adc_step_1_6_db_v0_1", pv="local_dev_v0_1"),
    )
    return run_id


def get_run_status(conn, run_id: str) -> dict | None:
    row = conn.execute(
        text(
            """
            select r.run_id, r.run_status, r.current_step_id, r.updated_at,
                   count(s.step_id) as steps_written
            from runs r
            left join step_records s on s.run_id = r.run_id
            where r.run_id = :r
            group by r.run_id
            """
        ),
        dict(r=run_id),
    ).mappings().first()
    return dict(row) if row else None


def finish_run(conn, run_id: str, status: str = "completed",
               actor: str = "system") -> None:
    """把 run 推到终态。

    write_step_record 每写一步就把 run_status 设成 running，但只有
    worker.process_job 会收尾。任何绕过 worker 的路径（集成测试、回填脚本、
    批处理）写完步骤后 run 会永久停在 running —— 线上就是没人管的僵尸 run，
    监控和告警全被它们淹掉。写完最后一步的调用方必须显式调用本函数。
    """
    if status not in TERMINAL_RUN_STATUSES:
        raise ValueError(f"{status!r} 不是终态，可选 {TERMINAL_RUN_STATUSES}")
    conn.execute(
        text("""update runs
                   set run_status = :s,
                       completed_at = now(),
                       updated_at = now()
                 where run_id = :r"""),
        dict(r=run_id, s=status),
    )
    audit(conn, run_id, f"run.{status}", actor=actor,
          target_type="run", target_id=run_id)


def reap_stale_runs(conn, older_than_minutes: int = 1440) -> int:
    """把长时间停在非终态的 run 标为 failed。按计划任务运行（本地 cron /
    AWS 上 EventBridge Scheduler -> Lambda，和 artifacts.reap_orphans 同一处）。

    超时阈值要大于最长一次完整流水线的耗时，否则会误杀正在跑的 run。
    """
    rows = conn.execute(
        text("""update runs
                   set run_status = 'failed',
                       updated_at = now()
                 where run_status not in ('completed', 'failed')
                   and updated_at < now() - (:m || ' minutes')::interval
             returning run_id"""),
        dict(m=older_than_minutes),
    ).scalars().all()

    for rid in rows:
        # run_status 只有 failed 一个失败终态，所以"超时被回收"和"真的执行失败"
        # 在那一列里分不开。往 step_errors 留一条带 error_type 的记录，排查时
        # 一眼能看出是哪种。
        conn.execute(
            text("""insert into step_errors (step_error_id, run_id, error_type,
                      error_message, retry_count)
                    values (:sid, :r, 'reaper_timeout', :msg, 0)"""),
            dict(sid=new_ulid("err"), r=rid,
                 msg=f"run 停留在非终态超过 {older_than_minutes} 分钟，被 reaper 回收"),
        )
        audit(conn, rid, "run.reaped", actor="reaper",
              target_type="run", target_id=rid,
              payload={"older_than_minutes": older_than_minutes})
    return len(rows)
