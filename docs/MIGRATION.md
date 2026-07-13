# Migration: Phase 1 Authorization to OPA/Cedar

## Current state (Phase 1)

Authorization is handled by `src/keystone_engage/auth.py`, a thin in-process policy check. This is structurally correct (tool calls that fail the policy check never execute) but operationally limited:

- Policy changes require code changes and redeployment
- No policy unit testing independent of the application
- No audit trail of policy evaluations separate from application audit
- The data plane carries a governance concern it should not own

The authorization input tuple is already agent-aware. Both `authorize_tool_call` and `authorize_retrieval` accept `agent_identity` alongside the caller identity. In v1 this is always `engagement-agent-v1`. In v2, different agents carry different authorization scopes, and the policy engine evaluates per-agent permissions without interface changes.

## Authorization input tuple

The OPA/Cedar input for every authorization decision:

```
{
  "user_identity":  "<caller_id or caller_role>",
  "agent_identity": "<which agent is requesting>",
  "resource":       "<tool_name or corpus_id>",
  "action":         "<requested_scope or 'retrieve'>",
  "arguments":      "<action_metadata dict>",
  "context":        "<session state, severity tier, tempo>"
}
```

v1 populates `agent_identity` with `engagement-agent-v1` on every call. v2 populates it per-agent from the agent registry. The policy engine evaluates the full tuple; the in-process module evaluates a subset (user_identity, resource, action).

## Target state (graduation path Stage 1.1)

A policy engine (OPA or Cedar) runs as a sidecar or service. The `authorize_tool_call` and `authorize_retrieval` interfaces stay the same; only the backend changes.

## Migration steps

1. **Choose engine.** OPA (Rego) for general-purpose, Cedar for structured RBAC+ABAC. If Keystone Counsel's caller-role-plus-client-relationship model drives the choice, Cedar is likely the better fit.

2. **Write policies.** Translate the `PolicyStore` rules to Rego or Cedar policy files. Store in a `keystone-policy` Git repository with CI syntax checks. Policies reference `agent_identity` to scope tool permissions per agent.

3. **Deploy engine.** Run OPA as a container on Data-Plane or Control-Plane. Configure bundle loading from the policy repo.

4. **Swap backend.** Replace the `PolicyStore` lookups in `auth.py` with HTTP calls to the policy engine. The `AuthorizationResult.decision_source` field changes from `"in-process"` to `"opa"`. The `AuthorizationResult.agent_identity` field is already populated.

5. **Verify.** Audit log entries show `decision_source: opa`. A policy change reloads without code change or restart. Negative tests (unauthorized calls) are denied with logged reasons. Agent-scoped tests confirm different agents get different permissions.

## What does not change

- The `authorize_tool_call` and `authorize_retrieval` function signatures
- The `AuthorizationResult` return type (including `agent_identity`)
- The structural guarantee: a denied call never executes
- The audit chain recording of authorization decisions
- The substrate fields on audit entries (agent_id, tempo, task_id, cost)

## Timeline

Stage 1.1 is targeted for weeks 5-10 of the 16-week plan, when Keystone Counsel's richer authorization model (caller role + client relationship + document classification) makes a policy engine valuable rather than premature.
