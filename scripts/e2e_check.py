"""端到端冒烟：提交一个 run，推动 relay + worker，断言八步全部落库。

CI 里作为最后一道关卡；本地也可以直接跑来确认全链路没坏。
    PYTHONPATH=. python scripts/e2e_check.py
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from app.db.session import make_engine
from app.services.run_service import submit_run
from app.workers import worker
from app.workers.outbox_relay import relay_tick
from app.workers.workflow import WORKFLOW


def main() -> int:
    e = make_engine()
    with e.begin() as c:
        _, resp = submit_run(c, user_id="e2e", route="POST /v1/runs",
                             body={}, idem_key=None)
    run_id = resp["run_id"]

    published = relay_tick(e)
    handled = worker.worker_tick(e)

    with e.begin() as c:
        status = c.execute(text("select run_status from runs where run_id=:r"),
                           {"r": run_id}).scalar()
        steps = c.execute(text("select count(*) from step_records where run_id=:r"),
                          {"r": run_id}).scalar()
        audits = c.execute(text("select count(*) from audit_events where run_id=:r"),
                           {"r": run_id}).scalar()

    expected = len(WORKFLOW)
    ok = (published >= 1 and handled and status == "completed" and steps == expected)
    print(f"run_id={run_id}")
    print(f"  relay 投递={published}  worker 处理={handled}")
    print(f"  run_status={status}  step_records={steps}/{expected}  audit_events={audits}")
    print("E2E:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
