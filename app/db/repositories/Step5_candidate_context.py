"""Step 5 — candidate context, candidates, materials, identifiers, links.

Material values: small ones (SMILES, short ids) are stored inline; large ones
(long FASTA, blobs) are pushed to the object store and referenced by
``value_artifact_id``. Exactly one of the two is set.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from ..tx import tx
from ..ids import new_ulid, summary_id
from ..projection import reset_step_projection
from . import artifacts
from .steps import write_step_record, upsert_tool_call

STEP_ID = "step_05_candidate_context"
_INLINE_MAX = 512  # bytes; above this, route the value to the object store


def _split_value(conn, store, run_id: str, m: dict):
    value = m.get("value", "")
    if value and len(value.encode()) > _INLINE_MAX:
        artifact_id = artifacts.store_artifact(
            conn, store, run_id=run_id, step_id=STEP_ID, artifact_type="tool_output",
            filename=f"material_{m['material_id']}.txt", body=value.encode(),
            content_type="text/plain",
        )
        return None, artifact_id
    return value or None, None


def write_candidate_context(conn, store, run_id: str, ctx: dict) -> str:
    with tx(conn):
        srid = write_step_record(
            conn, run_id=run_id, step_id=STEP_ID, step_order=5, status="succeeded",
            input_payload={}, output_payload=ctx,
            schema_version="step_05_candidate_context_v_current",
        )
        reset_step_projection(conn, run_id, STEP_ID)

        ctx_id = summary_id("ctx", run_id)
        conn.execute(
            text(
                """
                insert into candidate_contexts (candidate_context_id, run_id, step_record_id,
                  context_build_status, downstream_query_hints, enrichment_selection_audit, raw_payload)
                values (:ctx, :run_id, :srid, :cbs, cast(:dqh as jsonb), cast(:esa as jsonb), cast(:raw as jsonb))
                """
            ),
            dict(ctx=ctx_id, run_id=run_id, srid=srid,
                 cbs=ctx["context_build_status"],
                 dqh=json.dumps(ctx.get("downstream_query_hints", {})),
                 esa=json.dumps(ctx.get("enrichment_selection_audit", {})),
                 raw=json.dumps(ctx)),
        )

        for m in ctx.get("materials", []):
            inline, art_id = _split_value(conn, store, run_id, m)
            conn.execute(
                text(
                    """
                    insert into materials (run_id, material_id, material_type, value_inline,
                      value_artifact_id, value_format, extraction_status, validation_status,
                      role, role_status, raw_payload)
                    values (:run_id, :mid, :mtype, :vin, :vaid, :vfmt, :ext, :val,
                      :role, :rstatus, cast(:raw as jsonb))
                    """
                ),
                dict(run_id=run_id, mid=m["material_id"], mtype=m["material_type"],
                     vin=inline, vaid=art_id, vfmt=m.get("value_format"),
                     ext=m.get("extraction_status", "pending"),
                     val=m.get("validation_status", "unknown"),
                     role=m.get("role"), rstatus=m.get("role_status", "unknown"),
                     raw=json.dumps(m)),
            )

        for cand in ctx.get("candidates", []):
            _insert_candidate(conn, run_id, ctx_id, cand)

        # Step 5 output also carries tool_call_records (top-level, no lane)
        for tcr in ctx.get("tool_call_records", []):
            upsert_tool_call(conn, run_id, STEP_ID, tcr)
    return ctx_id


def _insert_candidate(conn, run_id: str, ctx_id: str, cand: dict) -> None:
    conn.execute(
        text(
            """
            insert into candidates (run_id, candidate_id, candidate_context_id,
              candidate_name, candidate_role, is_generated_candidate, context_status,
              candidate_status, source_records, data_gaps, missing_material_roles,
              context_notes, raw_payload)
            values (:run_id, :cid, :ctx, :name, :role, :gen, :cstatus, :status,
              :src, :gaps, :mmr, :notes, cast(:raw as jsonb))
            """
        ),
        dict(run_id=run_id, cid=cand["candidate_id"], ctx=ctx_id,
             name=cand.get("candidate_name"), role=cand.get("candidate_role", "unknown"),
             gen=cand.get("is_generated_candidate", False),
             cstatus=cand.get("context_status", "unknown"),
             status=cand.get("candidate_status"),
             src=cand.get("source_records", []), gaps=cand.get("data_gaps", []),
             mmr=cand.get("missing_material_roles", []),
             notes=cand.get("context_notes", []), raw=json.dumps(cand)),
    )
    for ident in cand.get("identifiers", []):
        conn.execute(
            text(
                """
                insert into candidate_identifiers (candidate_identifier_id, run_id,
                  candidate_id, id_type, id_value, source_ids, confidence)
                values (:id, :run_id, :cid, :it, :iv, :src, :conf)
                """
            ),
            dict(id=new_ulid("cid"), run_id=run_id, cid=cand["candidate_id"],
                 it=ident["id_type"], iv=ident["id_value"],
                 src=ident.get("source_ids", []), conf=ident.get("confidence", 0.0)),
        )
    # adc_links -> candidate_material_links (one row per material + role)
    ADC_LINK_ROLES = {
        "target_material_ids": "target",
        "antibody_material_ids": "antibody",
        "payload_material_ids": "payload",
        "linker_material_ids": "linker",
        "dar_material_ids": "dar",
    }
    for link_field, role in ADC_LINK_ROLES.items():
        for material_id in cand.get("adc_links", {}).get(link_field, []):
            conn.execute(
                text(
                    """
                    insert into candidate_material_links (candidate_material_link_id,
                      run_id, candidate_id, material_id, material_role, link_status)
                    values (:id, :run_id, :cid, :mid, :role, 'active')
                    on conflict (run_id, candidate_id, material_id, material_role) do nothing
                    """
                ),
                dict(id=new_ulid("cml"), run_id=run_id, cid=cand["candidate_id"],
                     mid=material_id, role=role),
            )
