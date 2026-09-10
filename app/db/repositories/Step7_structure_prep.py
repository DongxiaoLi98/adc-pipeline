"""Step 7 — prepared structure input package.

Follows the standard writer pattern (see docs/02 + liability.py):
    write_step_record (JSONB document-of-record)
    -> reset_step_projection (delete owned root, cascade wipes the subtree)
    -> rebuild normalized projection rows.

Storage discipline (v0.3 diff): NEVER persist raw PDB/CIF/FASTA/sequence text
here. Sequences are stored as length + sha256_prefix + an artifacts pointer.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from ..tx import tx
from ..ids import new_ulid, summary_id
from ..projection import reset_step_projection
from .steps import write_step_record

STEP_ID = "step_07_structure_prep"


def write_structure_prep(conn, run_id: str, pkg: dict) -> str:
    with tx(conn):
        srid = write_step_record(
            conn, run_id=run_id, step_id=STEP_ID, step_order=7,
            status="succeeded",   # 业务状态见 prepared_structure_packages.package_status
            input_payload={}, output_payload=pkg,
            schema_version="step_07_structure_prep_v_current",
        )
        reset_step_projection(conn, run_id, STEP_ID)

        pkg_id = summary_id("prepkg", run_id)
        conn.execute(
            text(
                """
                insert into prepared_structure_packages (prepared_structure_package_id,
                  run_id, step_record_id, package_status, preparation_warnings, raw_payload)
                values (:pid, :run_id, :srid, :status, cast(:warn as jsonb), cast(:raw as jsonb))
                """
            ),
            dict(pid=pkg_id, run_id=run_id, srid=srid,
                 status=pkg.get("package_status", "prepared"),
                 warn=json.dumps(pkg.get("preparation_warnings", [])),
                 raw=json.dumps(pkg)),
        )

        for inp in pkg.get("prepared_structure_inputs", []):
            conn.execute(
                text(
                    """
                    insert into prepared_structure_inputs (prepared_structure_input_id,
                      run_id, prepared_structure_package_id, candidate_id, input_status,
                      crystal_metadata, molecular_weight_estimate, raw_payload)
                    values (:id, :run_id, :pid, :cid, :status,
                      cast(:crystal as jsonb), cast(:mw as jsonb), cast(:raw as jsonb))
                    """
                ),
                dict(id=new_ulid("psi"), run_id=run_id, pid=pkg_id,
                     cid=inp.get("candidate_id"),
                     status=inp.get("input_status", "unknown"),
                     crystal=json.dumps(inp.get("crystal_metadata")) if inp.get("crystal_metadata") is not None else None,
                     mw=json.dumps(inp.get("molecular_weight_estimate")) if inp.get("molecular_weight_estimate") is not None else None,
                     raw=json.dumps(inp)),
            )

        for ref in pkg.get("sequence_refs_for_prediction", []):
            conn.execute(
                text(
                    """
                    insert into sequence_refs_for_prediction (sequence_ref_id, run_id,
                      prepared_structure_package_id, candidate_id, chain_role,
                      sequence_length, sha256_prefix, artifact_id, raw_payload)
                    values (:id, :run_id, :pid, :cid, :chain, :len, :sha, :art, cast(:raw as jsonb))
                    """
                ),
                dict(id=new_ulid("seqref"), run_id=run_id, pid=pkg_id,
                     cid=ref.get("candidate_id"), chain=ref.get("chain_role"),
                     len=ref.get("sequence_length"), sha=ref.get("sha256_prefix"),
                     art=ref.get("artifact_id"), raw=json.dumps(ref)),
            )
    return pkg_id
