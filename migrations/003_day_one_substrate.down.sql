-- Rollback of migration 002. Drops in reverse dependency order.

BEGIN;

DROP INDEX IF EXISTS idx_audit_task_id;
DROP INDEX IF EXISTS idx_audit_agent_id;

ALTER TABLE audit_entries
    DROP COLUMN IF EXISTS session_rolling_cost_cents,
    DROP COLUMN IF EXISTS latency_ms,
    DROP COLUMN IF EXISTS cost_cents,
    DROP COLUMN IF EXISTS model_used,
    DROP COLUMN IF EXISTS output_tokens,
    DROP COLUMN IF EXISTS input_tokens,
    DROP COLUMN IF EXISTS task_id,
    DROP COLUMN IF EXISTS tempo,
    DROP COLUMN IF EXISTS agent_id;

DROP TRIGGER IF EXISTS trigger_tasks_updated_at ON tasks;

DROP INDEX IF EXISTS idx_tasks_state;
DROP INDEX IF EXISTS idx_tasks_owner_agent;

DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS agents;

DROP TYPE IF EXISTS task_state;
DROP TYPE IF EXISTS agent_tempo;

-- set_updated_at() left in place; other tables may adopt it later.

COMMIT;