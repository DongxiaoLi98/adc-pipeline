"""Step 1 — raw request record + uploaded files.

Every uploaded file is registered as a canonical ``artifacts`` row (single
pointer table), and ``uploaded_files.artifact_id`` points at it. ``storage_path``
is kept only as a human-readable raw path for troubleshooting.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from ..tx import tx

from ..projection import reset_step_projection
from . import artifacts
from .steps import write_step_record

STEP_ID = "step_01_intake"


def write_intake(conn, store, run_id: str, raw_request: dict) -> str:
    with tx(conn):
        srid = write_step_record(
            conn, run_id=run_id, step_id=STEP_ID, step_order=1, status="succeeded",
            input_payload=raw_request, output_payload=raw_request,
            schema_version="step_01_intake_v_current",
        )
        reset_step_projection(conn, run_id, STEP_ID)  # idempotent

        rr_id = raw_request["raw_request_record_id"]
        conn.execute(
            text(
                """
                insert into raw_requests (raw_request_record_id, run_id, step_record_id,
                  entry_source, submitted_by, raw_user_query, intake_status, raw_payload)
                values (:rr, :run_id, :srid, :entry_source, :submitted_by,
                  :query, :status, cast(:raw as jsonb))
                """
            ),
            dict(rr=rr_id, run_id=run_id, srid=srid,
                 entry_source=raw_request["entry_source"],
                 submitted_by=raw_request.get("submitted_by"),
                 query=raw_request["raw_user_query"],
                 status=raw_request["intake_status"],
                 raw=json.dumps(raw_request)),
        )

        for f in raw_request.get("uploaded_files", []):
            # register bytes-or-pointer as a canonical artifact
            artifact_id = artifacts.store_artifact(
                conn, store, run_id=run_id, step_id=STEP_ID,
                artifact_type="uploaded_file", filename=f["original_filename"],
                body=f.get("_bytes", b""), content_type=f.get("content_type"),
            )
            conn.execute(
                text(
                    """
                    insert into uploaded_files (run_id, file_id, raw_request_record_id,
                      artifact_id, original_filename, storage_path, content_type,
                      sha256, size_bytes, related_candidate_id, related_candidate_ids,
                      role, chain_role, chain_id, chain_roles)
                    values (:run_id, :file_id, :rr, :aid, :fname, :path, :ctype,
                      :sha, :size, :rcid, :rcids, :role, :crole, :cid, cast(:croles as jsonb))
                    """
                ),
                dict(run_id=run_id, file_id=f["file_id"], rr=rr_id,
                     aid=artifact_id, fname=f["original_filename"],
                     path=f["storage_path"], ctype=f.get("content_type"),
                     sha=f.get("sha256"), size=f.get("size_bytes"),
                     rcid=f.get("related_candidate_id"),
                     rcids=f.get("related_candidate_ids", []),
                     role=f.get("role"), crole=f.get("chain_role"),
                     cid=f.get("chain_id"),
                     croles=json.dumps(f.get("chain_roles", {}))),
            )
    return rr_id
