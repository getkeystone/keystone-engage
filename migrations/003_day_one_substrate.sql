-- migrations/002_day_one_substrate.sql
--
-- Day-one substrate package for keystone-engage.
--
-- Adds the four substrate dimensions as first-class fields:
--   agent identity, tempo, task state, cost.
--
-- v1 populates with one agent (engagement-agent-v1) at fast tempo.
-- v2 populates with more. Schema is unchanged from v1 to v2.
--
-- Contact center heritage: this is the schema shape a multi-engine
-- contact center would have had if it had been designed as one
-- platform instead of federated engines. Agent identity is the
-- routing engine. Tempo is the SLA class. Task state is the
-- disposition machine. Cost is the per-interaction rate.

BEGIN;

-- Agent tempo: how fast an agent operates.
-- fast: sub-second. medium: seconds. slow: minutes. deferred: hours+.
CREATE TYPE agent_tempo AS ENUM ('fast', 'medium', 'slow', 'deferred');

-- Task lifecycle state.
-- v1 uses the minimal machine. v2 expands with claimed/stuck/rescheduled.
CREATE TYPE task_state AS ENUM ('created', 'in_progress', 'completed', 'failed');

-- Shared updated_at trigger function. Idempotent via CREATE OR REPLACE.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Agent registry. v1 has one entry.
CREATE TABLE agents (
    agent_id            TEXT PRIMARY KEY,
    agent_name          TEXT NOT NULL,
    agent_role          TEXT NOT NULL,
    tempo               agent_tempo NOT NULL,
    cost_profile        JSONB NOT NULL,
    registered_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the v1 engagement agent.
-- cost_profile values are placeholder-measured; replace with real numbers
-- from the current eval run when convenient.
INSERT INTO agents (agent_id, agent_name, agent_role, tempo, cost_profile)
VALUES (
    'engagement-agent-v1',
    'Engagement Agent',
    'engagement',
    'fast',
    '{
        "typical_input_tokens": 500,
        "typical_output_tokens": 300,
        "typical_latency_ms": 1000,
        "model_used": "qwen2.5:7b-instruct"
    }'::jsonb
);

-- Tasks. Every dispatched task is a row.
CREATE TABLE tasks (
    task_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_agent_id      TEXT NOT NULL REFERENCES agents(agent_id),
    state               task_state NOT NULL DEFAULT 'created',
    payload             JSONB NOT NULL,
    budget_cents        INTEGER NOT NULL CHECK (budget_cents >= 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_tasks_owner_agent ON tasks (owner_agent_id);
CREATE INDEX idx_tasks_state       ON tasks (state);

CREATE TRIGGER trigger_tasks_updated_at
BEFORE UPDATE ON tasks
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- Extend audit_entries with the four substrate dimensions.
-- Added nullable, backfilled to the v1 engagement agent, then required
-- fields (agent_id, tempo) enforced NOT NULL.
ALTER TABLE audit_entries
    ADD COLUMN agent_id                    TEXT REFERENCES agents(agent_id),
    ADD COLUMN tempo                       agent_tempo,
    ADD COLUMN task_id                     UUID REFERENCES tasks(task_id),
    ADD COLUMN input_tokens                INTEGER        CHECK (input_tokens               IS NULL OR input_tokens               >= 0),
    ADD COLUMN output_tokens               INTEGER        CHECK (output_tokens              IS NULL OR output_tokens              >= 0),
    ADD COLUMN model_used                  TEXT,
    ADD COLUMN cost_cents                  NUMERIC(12, 4) CHECK (cost_cents                 IS NULL OR cost_cents                 >= 0),
    ADD COLUMN latency_ms                  INTEGER        CHECK (latency_ms                 IS NULL OR latency_ms                 >= 0),
    ADD COLUMN session_rolling_cost_cents  NUMERIC(12, 4) CHECK (session_rolling_cost_cents IS NULL OR session_rolling_cost_cents >= 0);

-- Backfill: every pre-substrate audit entry belongs to the v1 engagement
-- agent at fast tempo. task_id and cost fields stay NULL for pre-substrate
-- rows since those dispatches did not carry those values.
UPDATE audit_entries
SET agent_id = 'engagement-agent-v1',
    tempo    = 'fast'
WHERE agent_id IS NULL;

-- Required substrate fields on audit_entries.
ALTER TABLE audit_entries
    ALTER COLUMN agent_id SET NOT NULL,
    ALTER COLUMN tempo    SET NOT NULL;

CREATE INDEX idx_audit_agent_id ON audit_entries (agent_id);
CREATE INDEX idx_audit_task_id  ON audit_entries (task_id);

COMMIT;