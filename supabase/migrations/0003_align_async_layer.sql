-- 0003_align_async_layer.sql
-- Aligns the async layer (0002) to the hardened patterns used across projects:
--   * outbox: full status lifecycle + backoff + DLQ terminal state
--   * api_idempotency_keys: composite PK (user_id, route, key) + cached response body
--   * job_queue: local stand-in for SQS (swap backend by env, not code)
-- The 0002 tables carry no data in dev (fresh `supabase db reset`), so we drop &
-- recreate rather than a multi-step ALTER. In prod this would be a data migration.

DROP TABLE IF EXISTS outbox;
DROP TABLE IF EXISTS api_idempotency_keys;

-- Transactional outbox: written in the SAME tx as the runs row. A relay claims
-- due rows (FOR UPDATE SKIP LOCKED), publishes to the queue, and applies backoff.
CREATE TABLE outbox (
  event_id             text PRIMARY KEY,                             -- ULID
  topic                text NOT NULL,                                -- 'run.submitted', 'step.completed'
  payload              jsonb NOT NULL DEFAULT '{}'::jsonb,           -- {"run_id": "..."}
  status               text NOT NULL DEFAULT 'pending'
                         CHECK (status IN
                           ('pending','publishing','published','retrying','dead')),  -- dead = DLQ terminal
  attempt_count        int  NOT NULL DEFAULT 0,
  last_error           text,
  locked_at            timestamptz,                                  -- relay claim marker
  next_attempt_at      timestamptz NOT NULL DEFAULT now(),           -- backoff; NOT NULL for clean index
  published_message_id text,                                         -- queue/SQS id, for dedupe
  created_at           timestamptz NOT NULL DEFAULT now(),
  published_at         timestamptz
);
-- Relay scans only due, unfinished work.
CREATE INDEX ix_outbox_due ON outbox (next_attempt_at)
  WHERE status IN ('pending','retrying');

-- API-level idempotency (distinct from step-level reset_step_projection).
-- Composite PK so keys are scoped per user+route; caches the response so a
-- retried request returns the SAME body, not just the same run_id.
CREATE TABLE api_idempotency_keys (
  user_id         text NOT NULL,
  route           text NOT NULL,                                     -- 'POST /v1/runs'
  key             text NOT NULL,                                     -- client Idempotency-Key
  request_hash    text NOT NULL,                                     -- canonical JSON fingerprint
  run_id          text REFERENCES runs(run_id) ON DELETE CASCADE,
  response_status int,
  response_body   jsonb,
  expires_at      timestamptz NOT NULL,                              -- TTL cleanup
  created_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, route, key)
);
CREATE INDEX ix_api_idem_expiry ON api_idempotency_keys (expires_at);

-- Local job queue = stand-in for Amazon SQS. The worker claims jobs with
-- FOR UPDATE SKIP LOCKED. Swapping to SQS changes app/workers/queue.py only.
CREATE TABLE job_queue (
  job_id          text PRIMARY KEY,                                  -- ULID
  topic           text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
  status          text NOT NULL DEFAULT 'ready'
                    CHECK (status IN ('ready','in_progress','done','failed')),
  attempt_count   int NOT NULL DEFAULT 0,
  locked_at       timestamptz,
  visible_at      timestamptz NOT NULL DEFAULT now(),                -- mimics SQS visibility timeout
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_jobq_ready ON job_queue (visible_at) WHERE status = 'ready';

-- Retries / DLQ reuse existing tables:
--   * per-step failures increment step_errors.retry_count (0001)
--   * an outbox row past its retry budget -> status='dead'; a run that fails ->
--     runs.run_status='failed'.
