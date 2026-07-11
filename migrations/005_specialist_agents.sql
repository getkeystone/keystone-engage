-- migrations/005_specialist_agents.sql
--
-- Register specialist agents for multi-agent Engage.
-- The substrate (agents table, task state machine, dispatch interface,
-- event bus) is already in place. This migration is the population
-- change: adding rows, not changing schema.
--
-- Contact center heritage:
--   empathy-agent    = IVR front-end distress screening
--   budget-agent     = rate cap enforcement per interaction
--   escalation-agent = supervisor routing engine
--   monitor-agent    = QM real-time monitoring
--
-- engagement-agent-v1 remains as the primary conversational agent.
-- The coordinator routes to specialists before or after the primary.

INSERT INTO agents (agent_id, agent_name, agent_role, tempo, cost_profile)
VALUES
    (
        'empathy-agent-v1',
        'Empathy Agent',
        'empathy',
        'fast',
        '{
            "typical_input_tokens": 100,
            "typical_output_tokens": 50,
            "typical_latency_ms": 200,
            "model_used": "rule-based"
        }'::jsonb
    ),
    (
        'budget-agent-v1',
        'Budget Agent',
        'budget',
        'fast',
        '{
            "typical_input_tokens": 0,
            "typical_output_tokens": 0,
            "typical_latency_ms": 5,
            "model_used": "rule-based"
        }'::jsonb
    ),
    (
        'escalation-agent-v1',
        'Escalation Agent',
        'escalation',
        'fast',
        '{
            "typical_input_tokens": 100,
            "typical_output_tokens": 50,
            "typical_latency_ms": 100,
            "model_used": "rule-based"
        }'::jsonb
    ),
    (
        'monitor-agent-v1',
        'Role Monitor Agent',
        'monitor',
        'deferred',
        '{
            "typical_input_tokens": 0,
            "typical_output_tokens": 0,
            "typical_latency_ms": 50,
            "model_used": "rule-based"
        }'::jsonb
    )
ON CONFLICT (agent_id) DO NOTHING;
