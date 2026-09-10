"""Transaction helper: join the caller's transaction instead of opening a new one.

Repository writers used to call ``conn.begin()`` unconditionally, which raises
InvalidRequestError when the caller already holds a transaction
(``with engine.begin() as conn: ...`` — the normal API/worker pattern).
``tx(conn)`` opens a transaction only when the connection is not already in one,
so a writer is usable both standalone and composed inside a bigger unit of work.
"""
from __future__ import annotations

from contextlib import nullcontext


def tx(conn):
    return nullcontext() if conn.in_transaction() else conn.begin()
