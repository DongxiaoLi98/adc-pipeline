"""不可变审计事件的统一写入口。

docs/04 / docs/05 把 audit_events 定位成受监管场景的审计轨迹 ——"每个状态
变更和人工操作都追加一条"。此前只有 run_service 和 worker 各自手写了一段
INSERT，八个 step 写入器一条都不写，冒烟跑完 audit_events 恒为 0。

统一走本模块有三个好处：
  1. action 命名有唯一出处，不会出现 "run.completed" / "run_completed" 并存
  2. 调用方不再重复拼 SQL
  3. 将来要加字段（比如 request_id、ip）只改一处

注意：audit_events 不在 projection._OWNED 里，所以 reset_step_projection
不会清它 —— 重跑一个步骤会**追加**新的审计行，历史保持完整。这是有意的：
审计表是 append-only 的，"这一步被重跑过三次"本身就是要留痕的事实。
"""
from __future__ import annotations

import json

from sqlalchemy import text

from .ids import new_ulid

# 已使用的 action 值。新增时在这里登记，避免同义词漂移。
ACTIONS = (
    "run.submitted",     # API 收到提交（run_service）
    "run.completed",     # 正常终结（finish_run）
    "run.failed",        # 失败终结（finish_run）
    "run.reaped",        # 超时被回收（reap_stale_runs）—— 与真实失败区分
    "run.blocked",       # Step 3 判定输入不足，转人工复核（worker）
    "step.written",      # 一条 step_record 落盘（write_step_record）
    "review.approve",    # 人工复核放行（POST /v1/runs/{id}/reviews）
    "review.reject",     # 人工复核否决
)


def record(conn, run_id: str | None, action: str, *,
           actor: str = "system",
           target_type: str | None = None,
           target_id: str | None = None,
           payload: dict | None = None) -> str:
    """追加一条审计事件。调用方需已持有事务 —— 审计必须和它描述的
    状态变更在同一个事务里提交，否则会出现"状态变了但没留痕"。"""
    audit_id = new_ulid("aud")
    conn.execute(
        text("""insert into audit_events (audit_event_id, run_id, actor, action,
                  target_type, target_id, event_payload)
                values (:aid, :run_id, :actor, :action, :tt, :tid,
                        cast(:payload as jsonb))"""),
        dict(aid=audit_id, run_id=run_id, actor=actor, action=action,
             tt=target_type, tid=target_id, payload=json.dumps(payload or {})),
    )
    return audit_id
