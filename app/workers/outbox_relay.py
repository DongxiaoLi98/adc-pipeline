"""Outbox relay — publishes committed outbox events to the queue.

Runs as its own loop/process. Claims due, unfinished events with
FOR UPDATE SKIP LOCKED (never a naive `WHERE published_at IS NULL` scan),
publishes them, and applies exponential backoff, sending exhausted events to
the DLQ terminal state ('dead').
"""
from __future__ import annotations

from sqlalchemy import text

from ..services.common import backoff, max_publish_attempts
from . import queue


def relay_tick(engine, batch: int = 10) -> int:
    """Publish up to `batch` due events. Returns how many were published."""
    published = 0
    with engine.begin() as conn:
        rows = conn.execute(
            text("""select event_id, topic, payload, attempt_count from outbox
                    where status in ('pending','retrying') and next_attempt_at <= now()
                    order by next_attempt_at
                    for update skip locked
                    limit :b"""),
            dict(b=batch),
        ).mappings().all()

        for ev in rows:
            try:
                msg_id = queue.publish(conn, ev["topic"], dict(ev["payload"]))
                conn.execute(
                    text("""update outbox
                            set status = 'published', published_at = now(),
                                published_message_id = :mid, locked_at = now()
                            where event_id = :eid"""),
                    dict(mid=msg_id, eid=ev["event_id"]),
                )
                published += 1
            except Exception as e:  # publish failed -> backoff or DLQ
                attempt = ev["attempt_count"] + 1
                dead = attempt >= max_publish_attempts()
                conn.execute(
                    text("""update outbox
                            set status = case when :dead then 'dead' else 'retrying' end,
                                attempt_count = :attempt, last_error = :err,
                                next_attempt_at = now() + (:secs || ' seconds')::interval
                            where event_id = :eid"""),
                    dict(dead=dead, attempt=attempt, err=str(e)[:500],
                         secs=int(backoff(attempt).total_seconds()), eid=ev["event_id"]),
                )
    return published


def run_forever(engine, interval_seconds: float = 1.0):  # pragma: no cover
    import time
    while True:
        if relay_tick(engine) == 0:
            time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    from ..db.session import make_engine
    run_forever(make_engine())
