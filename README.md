# Keystone Engage

Governed conversational agent for regulated customer interaction.

## What this is

In regulated customer interactions such as collections, hardship programs, and complaint handling, a wrong answer, an unauthorized document retrieval, or an unlogged action carries legal, compliance, and operational consequence. A general chatbot treats policy, escalation, and audit as things that can be added later.

Keystone Engage is a governed conversational agent. It authorizes retrieval before retrieval runs, refuses when the evidence is insufficient or the request is out of scope, routes high-severity interactions to a human before it composes a response, and writes an auditable record of every step. Governance is enforced in the request path, not asserted in a system prompt.

Engage runs on customer-controlled infrastructure with local models through Ollama. Core inference has no external API dependency.

## Architecture

When a request reaches `POST /engage` ([api.py](src/keystone_engage/api.py)), the orchestrator ([orchestrator.py](src/keystone_engage/orchestrator.py)) runs a fixed pipeline:

1. Open a task and an audit entry.
2. Authorize retrieval for the caller role and corpus ([auth.py](src/keystone_engage/auth.py), `authorize_retrieval`). A denied request returns nothing and stops here.
3. Assess escalation risk ([escalation.py](src/keystone_engage/escalation.py), `check_escalation`). Crisis, legal, discrimination, regulatory, and supervisor triggers route to a human at the matching severity tier before any response is composed.
4. Classify intent ([intent.py](src/keystone_engage/intent.py), `check_intent`). Out-of-scope requests receive a scoped refusal.
5. Dispatch to the engagement agent through `LocalDispatcher` ([dispatch.py](src/keystone_engage/dispatch.py)), which runs the RAG pipeline ([rag.py](src/keystone_engage/rag.py)) over the authorized corpus (pgvector when a database is configured, in-memory otherwise).
6. Return the response with evidence and close the audit entry.

The served path runs a single engagement agent. Dispatch goes through a `Dispatcher` protocol so the same call site can target an in-process agent today or a remote agent later. Telemetry is wired by `setup_telemetry` ([observability.py](src/keystone_engage/observability.py)), which instruments the app with OpenTelemetry spans following the GenAI semantic conventions.

An alternate entry point ([api_v2f.py](src/keystone_engage/api_v2f.py)) exists with the multi-agent coordinator behind the `KEYSTONE_MULTI_AGENT` flag. With the flag off, which is the default, it runs the same single-agent orchestrator described above. With the flag on, it routes through the coordinator pipeline ([coordinator.py](src/keystone_engage/coordinator.py)). The Makefile serves `api.py`, so `api_v2f.py` is not the default served path. The coordinator is described under Development roadmap.

## Governance controls

These controls are enforced in the served path:

- **Retrieval authorization.** The caller role must be scoped to the requested corpus, checked before retrieval runs. Enforcement is at the corpus boundary, a role-to-corpus scope check, not a per-document classification filter. A denied retrieval returns nothing, not a filtered subset.
- **Fail-closed refusal.** Out-of-scope or insufficient-evidence requests are refused rather than answered.
- **Severity-tier human routing.** Escalation triggers route to human review before the response is composed, not after.
- **Audit trail.** Every step appends to a hash-chained SHA-256 ledger ([audit.py](src/keystone_engage/audit.py), `verify_chain` walks the chain and checks linkage). Each entry records the prior entry's hash. Hashing is plain SHA-256 over the entry and the prior hash, not a keyed HMAC. The database role is INSERT-only.
- **Cost and telemetry.** Each request records token counts, model, latency, and cost fields in the audit entry and the OTel span.

## Evaluation

Two sealed runs are kept side by side rather than overwritten.

`keystone-engage/agent-v1` is the passing baseline at 100/100: core-regression 70/70, architecture 25/25, edge-case 5/5. `keystone-engage/agent-v0` is a failing run at 96/100, preserved as an artifact. Its four failures surfaced real bugs: a scope default on empty caller_id, an escalation regex gap, LLM non-determinism on keyword matching, and a missing pre-RAG empathy gate. All four were fixed in v1.

The eval exercises the served `/engage` endpoint over HTTP, so the results describe the single-agent served path, not the coordinator. Cases span 15 categories including payment arrangements, hardship, escalation, and out-of-scope refusal.

The runs were sealed at commit `d199382`. At that commit the served orchestrator ran an inline empathy gate (`check_empathy`) as part of the request path, which is what the empathy-category cases exercised. That gate has since been refactored into the coordinator and is no longer in the current served orchestrator. The current `api.py` default path does not run empathy detection. See Known drift below.

## Contact-center heritage

Engage rebuilds operational discipline that enterprise contact centers developed before LLMs:

- Severity-tier human routing maps to bot-to-human escalation with severity classification.
- Frame-based dialog slot validation maps to per-step evidence gating.
- The hash-chained audit ledger maps to compliance logging.
- Confidence-threshold refusal at retrieval maps to fail-closed handling.
- A preserved failing run maps to quality management: bad calls are analyzed, not buried.

## Development roadmap

The following exist as code or design but are not in the served path. They are labeled here as in progress, not current capability.

- **Multi-agent coordinator** ([coordinator.py](src/keystone_engage/coordinator.py)). A five-phase pipeline with per-agent audit trails, short-circuit gates, and budget enforcement. It exists as code with its own tests ([test_coordinator.py](tests/test_coordinator.py)) and is reachable through `api_v2f.py` behind `KEYSTONE_MULTI_AGENT`. The served `/engage` route does not use it, and it is not eval-covered.
- **Empathy agent** ([empathy.py](src/keystone_engage/empathy.py)). A real module with `check_empathy` that detects diffuse distress before RAG. It currently runs only in the coordinator path, not in the served orchestrator. See Known drift below.
- **Per-tool authorization** ([auth.py](src/keystone_engage/auth.py), `authorize_tool_call`). Per-tool checks scoped by agent identity. Defined and unit-tested, not yet called in any request path.
- **Remote dispatch.** The `Dispatcher` protocol and `LocalDispatcher` are served. A remote `A2ADispatcher` for agent-to-agent communication is a documented interface, not yet implemented.
- **Budget and Monitor agents.** Registered as agent identities with no implementation files. The coordinator carries their logic as inline methods (budget pre-check and cost record, async monitor dispatch). They are not built as standalone agents.

## Known drift

The 100/100 eval was sealed at commit `d199382`, where the served orchestrator ran an inline empathy gate (`check_empathy`). That gate was later refactored into the coordinator ([coordinator.py](src/keystone_engage/coordinator.py)) and removed from the served orchestrator. The current `api.py` default path does not run empathy detection. This is a known gap between the eval'd path and the current served path, not a claim that empathy screening runs by default today.

## Related repos

- [keystone-ledger](https://github.com/getkeystone/keystone-ledger): eval lineage and public proof artifacts.
- [keystone-verify](https://github.com/getkeystone/keystone-verify): the evaluation framework as a standalone tool. Public.
- [keystone-counsel](https://github.com/getkeystone/keystone-counsel): authorization-first retrieval for regulated content.
- [keystone-gov](https://github.com/getkeystone/keystone-gov): governed RAG reference implementation.

## License

Apache-2.0. See [LICENSE](LICENSE).
