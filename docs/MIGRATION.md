# Migration: Phase 1 Authorization to OPA/Cedar

## Current state (Phase 1)

Authorization is handled by `src/keystone_engage/auth.py`, a thin in-process policy check. This is structurally correct (tool calls that fail the policy check never execute) but operationally limited:

- Policy changes require code changes and redeployment
- No policy unit testing independent of the application
- No audit trail of policy evaluations separate from application audit
- The data plane carries a governance concern it should not own

## Target state (graduation path Stage 1.1)

A policy engine (OPA or Cedar) runs as a sidecar or service. The `authorize_tool_call` and `authorize_retrieval` interfaces stay the same; only the backend changes.

## Migration steps

1. **Choose engine.** OPA (Rego) for general-purpose, Cedar for structured RBAC+ABAC. If Keystone Counsel's caller-role-plus-client-relationship model drives the choice, Cedar is likely the better fit.

2. **Write policies.** Translate the `PolicyStore` rules to Rego or Cedar policy files. Store in a `keystone-policy` Git repository with CI syntax checks.

3. **Deploy engine.** Run OPA as a container on AnchorNode or ForgePrime. Configure bundle loading from the policy repo.

4. **Swap backend.** Replace the `PolicyStore` lookups in `auth.py` with HTTP calls to the policy engine. The `AuthorizationResult.decision_source` field changes from `"in-process"` to `"opa"`.

5. **Verify.** Audit log entries show `decision_source: opa`. A policy change reloads without code change or restart. Negative tests (unauthorized calls) are denied with logged reasons.

## What does not change

- The `authorize_tool_call` and `authorize_retrieval` function signatures
- The `AuthorizationResult` return type
- The structural guarantee: a denied call never executes
- The audit chain recording of authorization decisions

## Timeline

Stage 1.1 is targeted for weeks 5-10 of the 16-week plan, when Keystone Counsel's richer authorization model (caller role + client relationship + document classification) makes a policy engine valuable rather than premature.
