"""Integration test for the async backend slice.

Skipped automatically unless DATABASE_URL is set (same convention as the other
integration tests). Covers: API-level idempotency (same key -> same run), and
the outbox -> relay -> queue -> worker -> step_records path.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="integration test needs DATABASE_URL"
)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.api.main import app
    return TestClient(app)


def test_post_run_is_idempotent(client):
    h = {"Authorization": "Bearer dev-submitter", "Idempotency-Key": "k-abc"}
    r1 = client.post("/v1/runs", json={}, headers=h)
    r2 = client.post("/v1/runs", json={}, headers=h)
    assert r1.status_code == 202
    assert r1.json()["run_id"] == r2.json()["run_id"]  # replay, no second run


def test_idempotency_key_reuse_different_body_conflicts(client):
    h = {"Authorization": "Bearer dev-submitter", "Idempotency-Key": "k-xyz"}
    client.post("/v1/runs", json={"project_id": "p1"}, headers=h)
    r = client.post("/v1/runs", json={"project_id": "p2"}, headers=h)
    assert r.status_code == 409


def test_rbac_viewer_cannot_submit(client):
    r = client.post("/v1/runs", json={}, headers={"Authorization": "Bearer dev-viewer"})
    assert r.status_code == 403


def test_outbox_relay_to_worker(client):
    from app.api.deps import engine
    from app.workers import worker
    from app.workers.outbox_relay import relay_tick

    eng = engine()
    run_id = client.post(
        "/v1/runs", json={}, headers={"Authorization": "Bearer dev-submitter"}
    ).json()["run_id"]

    assert relay_tick(eng) >= 1          # outbox -> job_queue
    # Drain: earlier tests may have left jobs queued, so process until ours is done.
    for _ in range(20):
        if not worker.worker_tick(eng):
            break

    r = client.get(f"/v1/runs/{run_id}", headers={"Authorization": "Bearer dev-viewer"})
    body = r.json()
    assert body["run_status"] == "completed"
    assert body["steps_written"] >= 1
