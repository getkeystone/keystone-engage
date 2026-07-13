# Keystone Engage Architecture

## Position in the deployment planes

Keystone Engage runs as agent processes on the **control plane** (Control-Plane), with:

- **Dialog state** held in the **data plane** (Data-Plane, PostgreSQL) as structured frame slots
- **Inference** dispatched to the **inference plane** (Inference-Plane, Ollama) over HTTP
- **Audit records** hash-chained to the **data plane** and archived to the **storage plane**
- **Observability** emitted to the **observability plane** (Observability-Plane) via OTel GenAI conventions
- **Tools** exposed via MCP on the control plane

## Design choices and contact center heritage

Every design choice in Engage traces to a pre-LLM contact center AI pattern:

**Dialog state is structured, not free-form.** Frame-based slots with required fields that must be filled from verified sources before advancing. This is frame-based dialog slot validation from the contact center era: you could not advance to the next dialog state without filling the required slots.

**Authorization is a hard architectural layer.** Tool calls that fail the policy check never execute. This is not guardrail-based filtering; it is structural prevention. The auth module enforces this in-process for Phase 1 and migrates to OPA/Cedar in graduation path Stage 1.1.

**Fail-closed at retrieval.** If retrieval confidence is below threshold, the system returns nothing rather than a best-effort answer. The orchestrator routes to HITL based on severity tier. This is confidence-threshold escalation from bot deployments.

**Audit is mandatory per turn.** Every request opens an audit entry. Every response closes one with full provenance (query, retrieved chunks, model, confidence, decision). The chain is hash-linked and append-only. This is contact center compliance logging.

## Orchestration topology

Supervisor/orchestrator-worker. Single orchestrator (the EngageOrchestrator class) directs retrieval, generation, evidence gating, and severity routing. Pure orchestration by choice; the choreography option is graduation path Stage 2.2.

## Severity-tier HITL routing

| Tier | Behavior | Heritage mapping |
|------|----------|------------------|
| Tier 0 | Fully automated, no review | Low-confidence bot response, auto-resolved |
| Tier 1 | Automated with post-hoc review queue | Bot response queued for QA sampling |
| Tier 2 | Human review required before response | Bot-to-human escalation |
| Tier 3 | Immediate escalation, no automated response | Critical escalation, supervisor routing |
