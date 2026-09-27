BEGIN;

-- New installations start live through schema.sql. Existing operator intent
-- is data, so this migration changes only the default and never the row.
ALTER TABLE scheduler_control ALTER COLUMN running SET DEFAULT true;

CREATE TABLE IF NOT EXISTS gold_control (
    id                   boolean PRIMARY KEY DEFAULT true CHECK (id),
    generation           bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    processed_generation bigint NOT NULL DEFAULT 0
        CHECK (processed_generation >= 0 AND processed_generation <= generation),
    dirty_at             timestamptz,
    not_before           timestamptz,
    hold_until           timestamptz,
    last_started_at      timestamptz,
    last_completed_at    timestamptz,
    last_outcome         text NOT NULL DEFAULT 'never'
        CHECK (last_outcome IN ('never', 'running', 'success', 'error')),
    last_error           text
);
INSERT INTO gold_control (id) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS system_run (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_type    text NOT NULL
        CHECK (job_type IN ('feed', 'gold_catchup', 'dedup', 'token_heartbeat')),
    identity    text,
    state       text NOT NULL DEFAULT 'running'
        CHECK (state IN ('running', 'complete', 'degraded', 'failed', 'interrupted')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(detail) = 'object'),
    error       text,
    CHECK ((state = 'running') = (finished_at IS NULL))
);
CREATE INDEX IF NOT EXISTS system_run_started_idx ON system_run (started_at DESC);

COMMIT;
