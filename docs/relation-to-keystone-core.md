# Relation to keystone-core

Keystone Engage is a platform extension of Keystone Applied Intelligence, not a separate project. It shares foundational contracts with keystone-core and extends them into the governed conversational agent domain.

## Shared contracts

- **Audit chain format.** Same hash-chained JSONL with `prev_hash` and `curr_hash`. Same `AuditEntry` schema. Engage audit records are compatible with keystone-core audit tooling.
- **Fail-closed retrieval.** Same confidence-threshold contract. Below threshold returns nothing, not a degraded answer.
- **Eval methodology.** Same approach: failing runs preserved alongside passing runs, eval sets grow from adversarial discovery, results hash-chained to the audit ledger.
- **Eval naming.** `keystone-engage/agent-v1` follows the `keystone-{component}/{type}-v{n}` convention.

## What Engage adds

- **Frame-based dialog state.** Structured slot management for multi-step engagement journeys. keystone-core handles single-turn retrieval and agent tasks; Engage handles multi-turn governed conversations.
- **Severity-tier HITL routing.** Four-tier escalation model mapping to contact center bot-to-human escalation patterns.
- **Behavioral content library.** Curated response templates and engagement strategies for regulated customer interaction (collections, onboarding, compliance notifications).
- **MCP tool exposure.** Engage tools are exposed via Model Context Protocol from day one (graduation path 1.3). keystone-core will adopt MCP as well; Engage leads.
- **OTel GenAI conventions.** Engage emits OpenTelemetry spans with GenAI semantic conventions from day one (graduation path 1.4).

## Eval lineage

| Eval ID | Component | Status |
|---------|-----------|--------|
| keystone-core/retrieval-v1 | Core retrieval | Published |
| keystone-core/agent-v0 | Core agent (failing run) | Published |
| keystone-core/agent-v1 | Core agent (186 cases) | Published |
| keystone-engage/agent-v1 | Engage agent | Planned |
