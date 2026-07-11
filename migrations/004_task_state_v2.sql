-- migrations/004_task_state_v2.sql
--
-- Expand the task state machine for multi-agent operation.
-- Adds states for claim, heartbeat, stuck detection, takeover,
-- verification, and unrecoverable failure.
--
-- Contact center heritage: this is the full disposition machine.
-- Claimed = agent accepted the call. Stuck = agent went silent.
-- Rescheduled = call transferred to another agent. Verified =
-- supervisor reviewed the outcome.
--
-- NOTE: ALTER TYPE ADD VALUE cannot run inside BEGIN/COMMIT in
-- PostgreSQL. Each ADD VALUE is a separate statement.

ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'claimed_by_agent';
ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'stuck';
ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'rescheduled';
ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'completed_verified';
ALTER TYPE task_state ADD VALUE IF NOT EXISTS 'failed_unrecoverable';

-- Add heartbeat and takeover columns to tasks
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS last_heartbeat_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS heartbeat_interval_s INTEGER DEFAULT 30,
    ADD COLUMN IF NOT EXISTS previous_owner_id    TEXT,
    ADD COLUMN IF NOT EXISTS takeover_count       INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS stuck_reason         TEXT;

-- Index for finding stuck tasks (in_progress with stale heartbeat)
CREATE INDEX IF NOT EXISTS idx_tasks_heartbeat
    ON tasks (state, last_heartbeat_at)
    WHERE state = 'in_progress';
