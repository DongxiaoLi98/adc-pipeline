"""Artifact lifecycle test: pending -> available, and orphan reaping.

Integration test — needs a live local Postgres (Supabase Local). Skipped when
DATABASE_URL is unset so `pytest tests/test_ids.py` still runs unit-only.
"""
import os
import pytest
from sqlalchemy import text

from app.db.session import make_engine, make_object_store
from app.db.repositories import runs, artifacts

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ,
                                reason="needs a local Postgres")


def test_store_artifact_becomes_available():
    engine = make_engine()
    store = make_object_store()
    with engine.begin() as conn:
        run_id = runs.create_run(conn, user_id="tester")
        aid = artifacts.store_artifact(
            conn, store, run_id=run_id, step_id="step_01_intake",
            artifact_type="uploaded_file", filename="x.pdb", body=b"HEADER",
        )
        status = conn.execute(
            text("select upload_status from artifacts where artifact_id=:a"),
            {"a": aid},
        ).scalar()
        assert status == "available"


def test_reaper_deletes_stale_pending():
    engine = make_engine()
    with engine.begin() as conn:
        run_id = runs.create_run(conn, user_id="tester")
        aid = artifacts.register_pending(
            conn, run_id=run_id, step_id="step_01_intake",
            artifact_type="uploaded_file", bucket="b", key="k", uri="local://k",
        )
        # backdate it so the reaper considers it stale
        conn.execute(text("update artifacts set created_at = now() - interval '2 hours' "
                          "where artifact_id=:a"), {"a": aid})
        deleted = artifacts.reap_orphans(conn, older_than_minutes=60)
        assert deleted >= 1
