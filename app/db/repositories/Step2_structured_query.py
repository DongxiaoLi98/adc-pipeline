"""Step 2 — structured query, normalized entities, decompositions."""
from __future__ import annotations

import json

from sqlalchemy import text

from ..tx import tx
from ..ids import new_ulid, summary_id
from ..projection import reset_step_projection
from .steps import write_step_record

STEP_ID = "step_02_structured_query"


def write_structured_query(conn, run_id: str, sq: dict) -> str:
    with tx(conn):
        srid = write_step_record(
            conn, run_id=run_id, step_id=STEP_ID, step_order=2, status="succeeded",
            input_payload={}, output_payload=sq,
            schema_version="step_02_structured_query_v_current",
        )
        reset_step_projection(conn, run_id, STEP_ID)

        sq_id = summary_id("sq", run_id)
        ti = sq["task_intent"]
        me = sq.get("mentioned_entities", {})
        conn.execute(
            text(
                """
                insert into structured_queries (
                  structured_query_id, run_id, step_record_id, raw_request_record_id,
                  primary_intent, primary_intent_confidence, secondary_intents,
                  task_type, task_type_confidence, modality, modality_confidence,
                  user_goal_summary, target_or_antigen_text, disease_or_indication_text,
                  antibody_candidate_text, payload_text, linker_text,
                  referenced_inputs, requested_outputs, user_constraints,
                  parse_warnings, clarification_questions, raw_payload)
                values (:sq_id, :run_id, :srid, :rr,
                  :pi, :pic, :si, :tt, :ttc, :mod, :modc, :goal,
                  :tgt, :dis, :ab, :pay, :lnk,
                  cast(:refs as jsonb), :outs, cast(:cons as jsonb), :warn, :clar, cast(:raw as jsonb))
                """
            ),
            dict(sq_id=sq_id, run_id=run_id, srid=srid,
                 rr=sq["source_raw_request_ref"]["raw_request_record_id"],
                 pi=ti["primary_intent"], pic=ti.get("primary_intent_confidence"),
                 si=ti.get("secondary_intents", []), tt=ti.get("task_type"),
                 ttc=ti.get("task_type_confidence"), mod=ti.get("modality"),
                 modc=ti.get("modality_confidence"), goal=ti.get("user_goal_summary"),
                 tgt=me.get("target_or_antigen_text"),
                 dis=me.get("disease_or_indication_text"),
                 ab=me.get("antibody_candidate_text"), pay=me.get("payload_text"),
                 lnk=me.get("linker_text"),
                 refs=json.dumps(sq.get("referenced_inputs", [])),
                 outs=sq.get("requested_outputs", []),
                 cons=json.dumps(sq.get("user_constraints", [])),
                 warn=sq.get("parse_warnings", []),
                 clar=sq.get("clarification_questions", []),
                 raw=json.dumps(sq)),
        )

        for e in sq.get("normalized_entities", []):
            conn.execute(
                text(
                    """
                    insert into normalized_entities (normalized_entity_id, run_id,
                      structured_query_id, original_text, canonical_name, canonical_id,
                      canonical_id_source, entity_type, explicit_or_inferred, confidence, notes)
                    values (:id, :run_id, :sq_id, :ot, :cn, :cid, :cids, :et, :eoi, :conf, :notes)
                    """
                ),
                dict(id=new_ulid("ent"), run_id=run_id, sq_id=sq_id,
                     ot=e["original_text"], cn=e.get("canonical_name"),
                     cid=e.get("canonical_id"), cids=e.get("canonical_id_source"),
                     et=e.get("entity_type", "other"),
                     eoi=e.get("explicit_or_inferred", "inferred"),
                     conf=e.get("confidence", 0.0), notes=e.get("notes")),
            )
        for dec in sq.get("entity_decompositions", []):
            dec_id = new_ulid("dec")
            conn.execute(
                text(
                    """
                    insert into entity_decompositions (entity_decomposition_id, run_id,
                      structured_query_id, original_text, canonical_name, notes)
                    values (:id, :run_id, :sq_id, :ot, :cn, :notes)
                    """
                ),
                dict(id=dec_id, run_id=run_id, sq_id=sq_id,
                     ot=dec["original_text"], cn=dec.get("canonical_name"),
                     notes=dec.get("notes")),
            )
            for comp in dec.get("components", []):
                conn.execute(
                    text(
                        """
                        insert into entity_components (entity_component_id,
                          entity_decomposition_id, role, canonical_name, component_type,
                          canonical_id, canonical_id_source, inferred, source, notes)
                        values (:id, :dec_id, :role, :cn, :ct, :cid, :cids, :inf, :src, :notes)
                        """
                    ),
                    dict(id=new_ulid("comp"), dec_id=dec_id, role=comp.get("role", "other"),
                         cn=comp["canonical_name"], ct=comp.get("component_type"),
                         cid=comp.get("canonical_id"), cids=comp.get("canonical_id_source"),
                         inf=comp.get("inferred", True), src=comp.get("source"),
                         notes=comp.get("notes")),
                )
    return sq_id


def read_for_readiness(conn, run_id: str) -> dict:
    sq = conn.execute(
        text("select * from structured_queries where run_id = :r"), dict(r=run_id)
    ).mappings().first()
    entities = conn.execute(
        text("select * from normalized_entities where run_id = :r order by created_at"),
        dict(r=run_id),
    ).mappings().all()
    files = conn.execute(
        text("select * from uploaded_files where run_id = :r"), dict(r=run_id)
    ).mappings().all()
    return {"structured_query": dict(sq) if sq else None,
            "normalized_entities": [dict(e) for e in entities],
            "uploaded_files": [dict(f) for f in files]}
