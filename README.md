# Keystone Engage

Keystone Engage extends the [Keystone Applied Intelligence](https://getkeystone.ai) platform into governed conversational agents for regulated customer interaction. It shares the audit chain, fail-closed contract, and eval methodology of keystone-core. Tool exposure is via Model Context Protocol (MCP); observability emits OpenTelemetry GenAI semantic conventions. The forthcoming baseline will be published as `keystone-engage/agent-v1` alongside the existing `keystone-core/retrieval-v1` and `keystone-core/agent-v1` evals.

## What this is

A governed conversational agent for regulated customer interaction: multi-step engagement journeys with a behavioral content library, severity-tier HITL routing, per-step evidence gating, HMAC action audit chain, and tool authorization as a hard architectural layer.

Every design choice traces to a pre-LLM contact center AI pattern modernized for the LLM substrate:

| Keystone Engage pattern | Contact center heritage |
|---|---|
| Severity-tier HITL routing | Bot-to-human escalation with formal severity classification |
| Per-step evidence gating | Frame-based dialog slot validation |
| HMAC action audit chain | Contact center compliance logging |
| Fail-closed at retrieval | Confidence threshold escalation in bot deployments |
| Published failing run alongside passing run | Contact center quality management |

## Architecture

Keystone Engage runs as agent processes on the control plane, with frame-based dialog state held in PostgreSQL, severity-tier HITL routing logic in the orchestrator, and tool authorization checked before any tool call fires. Inference calls go to the inference plane over HTTP (OpenAI-compatible contract). Conversation logs are hash-chained to the audit ledger.

See [docs/architecture.md](docs/architecture.md) for the full design and [docs/relation-to-keystone-core.md](docs/relation-to-keystone-core.md) for how Engage extends the platform.

## Stack

- Python 3.11+, uv
- FastAPI, Pydantic
- PostgreSQL 16 + pgvector
- qwen2.5:7b-instruct via Ollama (OpenAI-compatible HTTP)
- MCP for tool exposure
- OpenTelemetry GenAI semantic conventions
- Docker Compose (deployment)

## Quick start

```bash
# Install dependencies
uv sync

# Run the API server
make run

# Run tests
make test

# Run eval suite
make eval
```

## Eval lineage

This repo will publish `keystone-engage/agent-v1` as the baseline eval. The eval methodology follows keystone-core: every failing run is preserved alongside the passing run, the eval set grows from adversarial discovery, and results are hash-chained to the audit ledger.

Published keystone-core evals for reference:
- `keystone-core/retrieval-v1`: P@1=0.75, MRR=0.79, 8/8 adversarial ACL probes blocked
- `keystone-core/agent-v1`: 186 cases, 12 categories, 558 executions, 0 failures

## License

Apache 2.0
