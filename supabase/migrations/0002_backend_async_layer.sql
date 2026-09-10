-- 0002_backend_async_layer.sql
-- Adds the service-layer tables for the API + async backend (doc 05).
-- Additive only: does not touch Steps 1-6 tables. Portable to RDS unchanged.

-- Transactional outbox: written in the SAME tx as the runs row, so a job is
-- never lost (DB commit but queue down) nor ghosted (queue publish but tx
-- rollback). A relay publishes 'pending' rows to the queue (local | SQS).
create table if not exists outbox (
  event_id     text primary key,                                  -- ULID
  topic        text not null,                                     -- e.g. 'run.submitted', 'step.completed'
  payload      jsonb not null default '{}'::jsonb,                -- {"run_id": "...", "step_id": "..."}
  status       text not null default 'pending'
                 check (status in ('pending','published','dead')),-- 'dead' = DLQ (retries exhausted)
  attempts     int  not null default 0,
  last_error   text,
  locked_at    timestamptz,                                       -- claim marker for the relay/worker
  created_at   timestamptz not null default now(),
  published_at timestamptz
);
-- Relay scans only unpublished work; partial index keeps it cheap as the table grows.
create index if not exists ix_outbox_pending
  on outbox (created_at)
  where status = 'pending';

-- API-level idempotency (distinct from step-level reset_step_projection).
-- Maps a client Idempotency-Key to the run it created, so a retried POST /v1/runs
-- returns the same run instead of starting a second one. Postgres is the source
-- of truth here; a cache (Redis) may front it but does not replace it.
create table if not exists api_idempotency_keys (
  key           text primary key,                                 -- client Idempotency-Key header
  user_id       text,
  endpoint      text not null,                                    -- e.g. 'POST /v1/runs'
  request_hash  text not null,                                    -- body fingerprint; same key + different body -> 409
  run_id        text references runs(run_id) on delete cascade,
  response_code int,
  expires_at    timestamptz not null,                             -- TTL for cleanup
  created_at    timestamptz not null default now()
);
create index if not exists ix_api_idem_expiry on api_idempotency_keys (expires_at);

-- Retries / DLQ reuse existing tables:
--   * per-step failures increment step_errors.retry_count (already in 0001)
--   * a job past its retry budget sets outbox.status = 'dead' and runs.run_status = 'failed'
-- No new error tables needed.
