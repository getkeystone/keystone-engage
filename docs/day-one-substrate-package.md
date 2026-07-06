# Day-One Substrate Package

## What this is

The substrate package adds four dimensions to the keystone-engage schema, dispatch interface, audit chain, and OTel spans from the first commit. These dimensions make v2 multi-agent operation an incremental population change rather than an architectural refactor.

## The four dimensions

**Agent identity.** Every audit entry, every OTel span, and every dispatch call records which agent handled the work. The `agents` table is a registry with role, tempo, and cost profile. v1 has one entry: `engagement-agent-v1`. v2 adds entries without schema migration.

**Tempo.** Different agents operate at different time horizons. The orchestrator's dispatch interface carries a tempo expectation. The audit chain records the tempo of the agent that produced each entry. v1 registers one agent at `fast` tempo. v2 introduces agents at `medium`, `slow`, and `deferred` tempos, with the messaging-event plane bridging the differences.

**Task state.** Every dispatch creates a task record with explicit lifecycle: `created`, `in_progress`, `completed`, `failed`. The audit chain references the task. v1 uses the minimal state machine. v2 expands with `claimed_by_agent`, `stuck`, `rescheduled`, `completed_verified`, and `failed_unrecoverable` for multi-agent recovery.

**Cost.** Every audit entry records token consumption (`input_tokens`, `output_tokens`), model identity, dollar cost, latency, and session rolling cost. The dispatch interface carries a budget parameter. v1 reports cost as 0 for local inference (tokens and latency are the meaningful metrics). v2 introduces cost-aware dispatch and budget enforcement.

## Why from day one

The cost of adding these dimensions in v1 was approximately 8-12 hours of implementation work: two database tables, nine columns on `audit_entries`, four parameters on the dispatch interface, six OTel span attributes, and one helper function.

The cost of not adding them and retrofitting in v2 would be a schema migration on a live audit chain (breaking hash continuity for backfilled rows), an interface change propagated through every call site, a rebuild of the OTel span structure, and a re-run of the entire eval set against the new schema.

The contact center industry learned this lesson through multi-engine federation pain. Genesys built separate engines for voice, chat, workforce management, and analytics. Each engine had its own data model. Cross-engine capabilities required PM-to-PM negotiations, framework limitations, and costed change requests. The cost difference between "the schema already supports this" and "we need to add this field" was not 5%. It was a factor of ten or more, paid in organizational friction across multiple teams and release cycles.

The protocol between agents is the protocol between teams. Getting the protocol right on day one prevents the cost of getting it wrong later from compounding across the organization.

## What ships in v1

**Schema (migration 003):**
- `agents` table with `agent_id`, `agent_name`, `agent_role`, `tempo`, `cost_profile`, `registered_at`
- `tasks` table with `task_id`, `owner_agent_id`, `state`, `payload`, `budget_cents`, `created_at`, `updated_at`
- `audit_entries` extended with `agent_id`, `tempo`, `task_id`, `input_tokens`, `output_tokens`, `model_used`, `cost_cents`, `latency_ms`, `session_rolling_cost_cents`

**Runtime:**
- `TaskStore` creates and updates task rows per dispatch
- `PgAuditChain` writes substrate columns alongside the payload column
- Orchestrator threads substrate fields through every audit entry
- Session rolling cost tracked in memory per session

**Authorization:**
- `authorize_retrieval` and `authorize_tool_call` accept `agent_identity`
- Authorization input tuple documented for OPA migration: `{user_identity, agent_identity, resource, action, arguments, context}`

**OTel:**
- `invoke_agent` spans carry `keystone.agent_id`, `keystone.agent_tempo`, `keystone.task_id`, `keystone.priority`, `keystone.cost_cents`, `keystone.budget_remaining_cents`

**Constants in v1:**
- Agent: `engagement-agent-v1`
- Tempo: `fast`
- Priority: `0`
- Budget: 100 cents ($1.00 per turn)
- Cost: 0 (local inference)

## What does NOT ship in v1

- No second agent (v2, post-offer)
- No A2A protocol calls (v2, post-offer)
- No NATS messaging plane (graduation path Stage 2.2)
- No cost-aware dispatch (v2)
- No predictive cost models (v3)
- No dynamic tempo allocation (PhD direction)
- No multi-agent verification protocols (PhD direction)

Each deferred item has a documented phase in `keystone-future-architecture.md`.

## Migration note

The substrate columns on `audit_entries` are added as nullable. Existing rows are backfilled with `agent_id = 'engagement-agent-v1'` and `tempo = 'fast'`. NOT NULL enforcement ships in a follow-up migration after the audit writer is confirmed to populate the fields on every write. Rollback is available via `migrations/003_day_one_substrate.down.sql`.
