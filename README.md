# Keystone Engage

Governed conversational agents for regulated customer interaction.

Keystone Engage extends the Keystone Applied Intelligence platform into multi-agent systems for environments where what the AI says or does can carry compliance, legal, or operational consequences.

This is not a general-purpose chatbot. It is a governed agent system built for situations like collections, hardship programs, complaint handling, regulated service workflows, and other interactions where escalation, authorization, evidence, and auditability are first-class requirements.

## What it does

A customer message does not go straight to a language model.

Keystone Engage applies structural controls before, during, and after generation:

- **Empathy gate** checks for distress signals and ensures the response acknowledges them appropriately.
- **Escalation gate** checks for crisis signals, legal threats, discrimination complaints, regulatory complaints, and supervisor requests, then routes to a human at the correct severity tier.
- **Intent gate** checks whether the request is actually in scope.
- **Engagement agent** retrieves from approved content and generates a governed response through a local model.
- **Budget agent** wraps execution with cost checks and accounting.
- **Monitor agent** reviews activity asynchronously for quality and oversight.

The result is a multi-agent pipeline where conversational behavior is constrained by policy, evidence, and review mechanics instead of relying on prompt instructions alone.

## Why it exists

Modern agent systems are often built as if policy, escalation, and audit can be added later.

Regulated environments do not work that way.

Keystone Engage starts from an older operational discipline that enterprise contact centers had to develop years before LLMs: severity-tier escalation, per-step validation, compliance logging, explicit routing, and refusal under uncertainty. Engage rebuilds that discipline for the LLM substrate.

## Platform role

Keystone Engage is one extension in the broader Keystone platform:

- **Engage** proves governed conversational AI in the contact-center style domain.
- **Counsel** proves authorization-first retrieval for legal and financial content.
- **Verify** proves the evaluation methodology as a reusable tool.

Engage shares the Keystone substrate for agent identity, task lifecycle, audit chain format, dispatch abstraction, and evaluation lineage.

## Core architectural properties

These are structural properties, not prompt suggestions:

- governed multi-agent orchestration,
- severity-tier human-in-the-loop routing,
- per-step evidence gating,
- tool authorization as a hard boundary,
- hash-chained tamper-evident audit trails,
- fail-closed behavior when evidence is insufficient,
- local-first deployment with no external API dependency for core inference.

## Multi-agent substrate

The schema is designed for multi-agent orchestration from day one.

Each agent is registered with identity, role, tempo, and cost profile. Work is tracked through a task lifecycle instead of ad hoc function calls. Events are published for asynchronous monitoring and takeover. Dispatch is abstracted so the same orchestration surface can target local or remote agents.

This makes the system extensible by adding agents, not by rewriting orchestration.

## Observability and tooling

Keystone Engage exposes tools through the Model Context Protocol (MCP), which standardizes how LLMs invoke external tools.[web:100][web:102]

Observability follows OpenTelemetry GenAI semantic conventions so model calls, tool calls, token usage, and agent behavior can be traced using a shared telemetry vocabulary.[web:92][web:94][web:98]

## Current eval status

The current eval arc is preserved publicly rather than overwritten.

Published and in-progress results show:

- failing runs preserved alongside passing runs,
- adversarial discovery used to expand the eval set,
- governance controls evaluated as system behavior rather than described as intentions.

This follows the same Keystone discipline used elsewhere in the platform: claims are backed by eval artifacts, not presentation copy.

## Current stack

- Python 3.11+
- FastAPI
- PostgreSQL 16 + pgvector
- Ollama for local inference
- MCP for tool exposure
- OpenTelemetry GenAI semantic conventions
- Docker Compose

## Repo goals

This repository exists to prove that governed conversational AI can be implemented as infrastructure, not as prompt craft.

Specifically, it aims to show that:

- escalation can be architectural,
- authorization can be enforced before action,
- evidence gating can constrain generation,
- auditability can survive multi-agent execution,
- evaluation can surface real failures before deployment.

## Relation to the rest of Keystone

- [`keystone-counsel`](https://github.com/getkeystone/keystone-counsel) applies the same discipline to regulated retrieval.
- [`keystone-verify`](https://github.com/getkeystone/keystone-verify) extracts the evaluation methodology into a standalone tool.
- [`keystone-kdat`](https://github.com/getkeystone/keystone-kdat) tracks evaluation lineage and proof artifacts.

## License

Apache 2.0
