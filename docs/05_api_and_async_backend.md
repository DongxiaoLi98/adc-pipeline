# 05 — API & Async Backend Layer

This doc adds the **service layer** on top of the Step 1–6 data layer: a
versioned REST API, asynchronous pipeline execution, reliability, and
observability. It changes **nothing** in the data model (docs 00–02); it wraps
it. The design goal is the same as the rest of the project: production-shaped,
portable, and a configuration change away from AWS (doc 06).

Why this exists: the milestone in `FOR_REVIEW.md` was the database layer. The
data layer already gives us the hard parts — idempotent writes
(`reset_step_projection`), a document-of-record, immutable `audit_events`,
reproducibility fields (`schema_version` / `model_version` / `code_commit_sha`).
What is missing is the *moving parts* around it: how a run is submitted, how
Steps 1→6 actually get executed, retried, observed, and secured.

---

## 1. Layered model

```
client ──HTTP──▶ API (FastAPI /v1, JWT+RBAC, Idempotency-Key)
                   │  writes runs + outbox in ONE transaction
                   ▼
              outbox (Postgres)  ──relay──▶ queue (local | SQS)
                                                 │
                                                 ▼
                                   worker  (FOR UPDATE SKIP LOCKED)
                                     runs Step N  ──▶ step_records + projections + artifacts
                                     (idempotent: reset_step_projection then rebuild)
                                     on failure ──▶ step_errors.retry_count++, backoff, DLQ
                                     every action ──▶ audit_events (immutable)
```

Two disciplines carry over from the data layer and make the whole thing safe:
**(a)** the run/step tables are already the source of truth and idempotent, so a
redelivered job is harmless; **(b)** `audit_events` already captures immutable
history, so the async layer records every transition there.

---

## 2. What we add to the schema (migrations `0002` + `0003`)

`0002` introduced the async tables; **`0003` hardens them** to the patterns used
across the wider system (consistent story, interview-proof). Net new tables:

- **`outbox`** — transactional outbox. Solves the classic dual-write problem:
  the API writes the `runs` row and the "run.submitted" event **in the same DB
  transaction**; a relay publishes due rows to the queue. Hardened columns
  (`0003`): `event_id` (ULID), `topic`, `payload` jsonb, `status`
  (`pending|publishing|published|retrying|dead` — **`dead` is the DLQ terminal
  state**), `attempt_count`, `last_error`, `locked_at`, `next_attempt_at`
  (NOT NULL, drives backoff + the partial index), `published_message_id`
  (dedupe), `created_at`, `published_at`.
- **`api_idempotency_keys`** — API-level idempotency (distinct from step-level
  `reset_step_projection`). Hardened (`0003`): **composite PK
  `(user_id, route, key)`** + cached `response_status` / `response_body` so a
  retried `POST /v1/runs` returns the *same response*, not just the same
  `run_id`; `request_hash` (canonical JSON) → same key + different body = 409;
  `expires_at` TTL.
- **`job_queue`** — a local stand-in for Amazon SQS so the whole flow runs with
  zero cloud dependencies. The worker claims jobs with `FOR UPDATE SKIP LOCKED`
  and a `visible_at` column mimics SQS visibility timeout. `QUEUE_BACKEND=sqs`
  swaps it for real SQS via `app/workers/queue.py` — env change, not code
  change (same discipline as the object store).

Retries/DLQ reuse what exists: per-step failure increments
`step_errors.retry_count`; an outbox row that exhausts its publish budget flips
`status='dead'` (DLQ) and a failed run goes `run_status='failed'`.

---

## 3. REST API contract (`/v1`, Bearer JWT)

Versioned under `/v1`; breaking changes open `/v2` and never mutate `/v1`
(backward compatibility). Auth is a Bearer JWT; because it is Bearer (not
cookie) CSRF is not the primary risk. RBAC roles: `submitter`, `reviewer`,
`viewer`, `admin`.

| Method & Path | Purpose | RBAC | Success | Errors |
|---|---|---|---|---|
| `POST /v1/auth/token` | issue JWT | public | 200 | 401 |
| `POST /v1/runs` | submit a pipeline run | submitter | 202 `{run_id,status}` | 401/403/409(idem)/422/429 |
| `GET /v1/runs/{id}` | run status + steps_written | viewer+ | 200 | 401/404 |
| `GET /v1/runs/{id}/steps` | per-step status/order | viewer+ | 200 | 404 |
| `GET /v1/runs/{id}/steps/{step_id}` | one step's output (from `step_records`) | viewer+ | 200 | 404 |
| `GET /v1/runs/{id}/artifacts` | artifact list (+ presigned download URLs) | viewer+ | 200 | 404 |
| `POST /v1/runs/{id}/uploads` | request presigned PUT for an input file | submitter | 201 `{upload_url}` | 403/422 |
| `GET /v1/runs/{id}/reviews` | Step-6 human reviews | reviewer+ | 200 | 404 |
| `POST /v1/runs/{id}/reviews` | submit a human review decision | **reviewer** | 200 | 403/404/409 |
| `GET /v1/runs/{id}/errors` | `step_errors` for debugging | viewer+ | 200 | 404 |
| `GET /healthz` `/readyz` | liveness / readiness (DB+queue+S3) | public | 200/503 | 503 |

Error taxonomy: 400 malformed · 401 unauthenticated · 403 RBAC · 404 missing ·
409 conflict (idempotency-key reuse, illegal state transition) · 422 validation
· 429 rate limit · 503 dependency down.

`POST /v1/runs` handler (transaction sketch):

```python
def submit_run(conn, user, body, idem_key):
    if idem_key:
        hit = get_idem(conn, idem_key)
        if hit and hit.request_hash != hash_body(body):
            raise Conflict("Idempotency-Key reused with different body")   # 409
        if hit:
            return hit.run_id                                              # replay
    with conn.begin():                                                     # one tx
        run_id = create_run(conn, user.id, body.project_id)                # runs (existing)
        if idem_key:
            insert_idem(conn, idem_key, user.id, "/v1/runs", body, run_id)
        insert_outbox(conn, "run.submitted", {"run_id": run_id})           # same tx
        audit(conn, run_id, actor=user.id, action="run.submitted")         # audit_events
    return run_id                                                          # 202
```

---

## 4. Async pipeline execution

The pipeline Steps 1→6 become an orchestrated job, not an inline call. Reuse the
existing state fields: `runs.run_status`, `runs.current_step_id`,
`step_records.step_order` / `step_status`.

**Run state machine** (values already in the schema):
`created → running → awaiting_review → completed`; `running → failed`;
`running →(step failed, retries left)→ running`; a step needing Step-6 human
sign-off sets `run_status='awaiting_review'` until `POST /reviews` resolves it.

**Worker loop** (portable; local queue now, SQS later — same interface):

```python
def worker_tick(conn):
    job = claim_one(conn)          # SELECT ... FOR UPDATE SKIP LOCKED  (no double-run)
    if not job: return
    run_id, step_id = job.run_id, job.step_id
    try:
        with conn.begin():
            reset_step_projection(conn, run_id, step_id)   # idempotency (existing)
            run_step(conn, run_id, step_id)                # writes step_records + projections + artifacts
            advance_or_finish(conn, run_id, step_id)       # enqueue next step or mark done
        publish_done(conn, job)                            # outbox → next
    except Exception as e:
        record_step_error(conn, run_id, step_id, e)        # step_errors.retry_count++
        if retries_left(conn, run_id, step_id):
            requeue_with_backoff(conn, job)                # exponential backoff
        else:
            dead_letter(conn, job); fail_run(conn, run_id) # outbox.status='dead', run_status='failed'
```

**Orchestration options** (both keep the worker above):
- *Now / portable*: a driver enqueues Step N+1 when Step N finishes (chained via
  outbox). Simple, runs locally.
- *AWS*: **Step Functions** state machine drives Step 1→6 with per-state
  retry/catch; the worker becomes a task. Mapping in doc 06.

**The reaper** (answers open question #4 in doc 04): `reap_orphans()` runs as a
**scheduled job**, not ad-hoc — a cron/worker locally, **EventBridge Scheduler →
Lambda** on AWS (doc 06). Give it a timeout > the longest expected upload.

---

## 5. Reliability

- **Idempotency at two layers**: API (`api_idempotency_keys`) stops duplicate
  runs; step (`reset_step_projection`) makes any redelivery safe.
- **Retries + backoff + DLQ**: per-step `retry_count`; exhausted → `outbox`
  DLQ + `run_status='failed'`; failed runs are inspectable via `step_errors`.
- **Graceful degradation**: a failing external tool call is recorded in
  `tool_call_records` / `step_errors`; the run fails cleanly with a resumable
  record rather than a half-written state (the write is transactional).
- **Health/readiness**: `/readyz` checks DB + queue + object store so the load
  balancer / ECS only routes to ready tasks.

## 6. Observability

- **Structured logs + correlation id**: `run_id` (and `step_id`) as a trace
  attribute on every log line and span, so one run is greppable end-to-end
  across API → queue → worker.
- **OpenTelemetry**: auto-instrument FastAPI + SQLAlchemy + boto3; export to
  the OTel Collector (CloudWatch/X-Ray on AWS, doc 06).
- **Metrics**: runs submitted/succeeded/failed, per-step latency, queue depth,
  retry rate, reaper reclaims, `outbox` lag (unpublished age). These become
  CloudWatch alarms in doc 06.

## 7. Security / PII (extends doc 04)

- **AuthN/Z**: JWT + RBAC; `POST /reviews` is reviewer-only; artifact downloads
  are **presigned URLs with short expiry**, never public objects.
- **PII**: `raw_requests.submitted_by` stays out of object keys (already a rule
  in doc 04); the API never logs it; RBAC gates who can read intake records.
- **Secrets**: DB/S3 creds come from env now, **Secrets Manager** on AWS; never
  in the DB or logs.
- **Audit**: every state transition and human action is appended to
  `audit_events` (immutable) — this is the regulated-domain audit trail.

---

## 8. Why this layer matters for the job target (backend-first)

This project already demonstrates the pieces most backend / AI-backend JDs ask
for by name. Once this layer lands, the résumé bullets it supports (with real
code to back them in interview):

- *Designed idempotent, event-driven backend services with a transactional-outbox
  pattern for reliable delivery between Postgres and the queue; retries, DLQ,
  failure recovery.* → §2, §4, §5
- *Modeled a document-of-record + relational projection with immutable audit
  events and reproducibility metadata for full auditability.* → data layer + §7
- *Built a versioned REST API with JWT/RBAC, API-level idempotency, and
  human-in-the-loop review gates in a regulated-style flow.* → §3, §7
- *Orchestrated a multi-step pipeline (Step Functions / worker) with per-step
  retry, backoff, and dead-lettering.* → §4
- *Instrumented the service with OpenTelemetry tracing and correlation ids.* → §6

New reading order: docs 00→02 (data), **05 (this)**, 06 (AWS production).
