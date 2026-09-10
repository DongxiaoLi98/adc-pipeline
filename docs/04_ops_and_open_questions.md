# 04 — Ops, Governance & Open Questions

## Governance tables (in the schema)

- **`audit_events`** — append-only log of human review, manual override, retry,
  skipped step, schema migration, artifact replacement. This is where
  *immutable* history goes; projection tables (like `tool_call_records`) may be
  rebuilt, so anything that must never change is written here as well.
- **`human_reviews`** — Step 6 can emit `review` / `human_review`; this table
  tracks reviewer, decision, status. Composite-FK'd to `(run_id, candidate_id)`.
- **`step_errors`** — error type/message/traceback + `retry_count` for failed
  steps and tool calls.

## Snapshot vs overwrite

`reset_step_projection()` de-duplicates **same-run reruns** — that is not the
same as versioning. To keep historical snapshots of ranked outputs / active
flags, use a new `run_id` or an artifact version chain (`artifacts.parent_artifact_ids`,
`is_active`). The two mechanisms don't conflict: reset cleans within a run,
snapshots live across runs.

## Security / data governance (local phase too)

- No secrets in the DB; no full private file contents in the DB (hash + pointer
  only).
- Stable artifact paths; PII (`raw_requests.submitted_by`) never in object keys.
- Draft user/project access control now with Supabase RLS; maps to IAM/S3
  policies after migration.

## Production-hardening status (from review)

| # | Item | Status in this scaffold |
|---|---|---|
| 1 | Supabase Storage ≠ full S3 (no S3 versioning) | Noted in doc 02 + ADR-2 |
| 2 | `artifacts` needs bucket/object_key/backend/upload_status/etag + orphan handling | Done — schema + `repositories/artifacts.py` lifecycle + `reap_orphans()` |
| 3 | candidate/material/file id uniqueness | Done — run-scoped composite PKs (ADR-5, doc 01) |
| 4 | `tool_call_records` = projection or audit? | Decided — projection; immutable history → `audit_events` (doc 02) |
| 5 | run-scoped composite foreign keys | Done — composite FKs in migration 0001 |
| — | ID generation contract undefined (was `deterministic_id(...)` with no def) | Done — `app/db/ids.py` (ULID + uuid5) |

## Open questions (decide before production spec sign-off)

1. **ULID dependency** — confirm `python-ulid` is acceptable, or switch to a
   uuid7/ksuid equivalent. `ids.py` falls back to uuid4 if absent.
2. **Materials inline threshold** — `_INLINE_MAX` is 512 bytes as a placeholder;
   pick the real cutoff per material type (SMILES inline, FASTA to object store?).
3. **~~`entity_decompositions` / `candidate_material_links` stubs~~** — DONE:
   writers are now implemented in `structured_query.py` / `candidate_context.py`.
   Confirm the exact upstream payload keys match (`components`, `adc_links.*`).
4. **Reaper cadence** — where does `reap_orphans()` run (cron? worker?) and what
   timeout?
5. **Retention** — how long do `step_records.output_payload` and artifacts live
   before archival to cheaper storage?
6. **Enum freeze** — which enum vocabularies are stable enough to add CHECK
   constraints (ADR-4)?
