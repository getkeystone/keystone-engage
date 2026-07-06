"""Phase 1 authorization for Keystone Engage.

Thin in-process policy check. Tool authorization is a hard architectural layer,
not prompt-mediated. This module enforces authorization structurally: a tool call
that fails the policy check never executes, regardless of what the LLM requested.

Authorization input tuple (v1 in-process, v2 OPA):
  {user_identity, agent_identity, resource, action, arguments, context}

In v1, agent_identity is always "engagement-agent-v1". In v2, different agents
have different authorization scopes. The interface stays the same; only the
backend changes when OPA ships in Phase 2.

MIGRATION: This module is replaced by OPA or Cedar in graduation path Stage 1.1.
See docs/MIGRATION.md for the externalization plan. The interface (authorize_tool_call,
authorize_retrieval) stays the same; only the backend changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from keystone_engage.models import ToolPermission

logger = logging.getLogger(__name__)


@dataclass
class AuthorizationResult:
    allowed: bool
    reason: str
    decision_source: str = "in-process"  # becomes "opa" after migration
    agent_identity: str = ""  # which agent requested this authorization


@dataclass
class PolicyStore:
    """In-process policy store. Replaced by OPA bundle in Stage 1.1."""

    tool_permissions: dict[str, ToolPermission] = field(default_factory=dict)
    retrieval_scopes: dict[str, list[str]] = field(default_factory=dict)

    def register_tool(self, permission: ToolPermission) -> None:
        self.tool_permissions[permission.tool_name] = permission

    def register_retrieval_scope(self, caller_role: str, allowed_corpora: list[str]) -> None:
        self.retrieval_scopes[caller_role] = allowed_corpora


# Module-level store, initialized at startup
_policy_store = PolicyStore()


def get_policy_store() -> PolicyStore:
    return _policy_store


def authorize_tool_call(
    tool_name: str,
    caller_id: str,
    requested_scope: str,
    action_metadata: dict[str, Any] | None = None,
    agent_identity: str = "",
) -> AuthorizationResult:
    """Check whether a tool call is authorized.

    This is the structural gate. A denied call never executes.
    The interface stays the same when the backend moves to OPA.

    Authorization input tuple for OPA migration:
      user_identity  = caller_id
      agent_identity = which agent is requesting (v1: engagement-agent-v1)
      resource       = tool_name
      action         = requested_scope
      arguments      = action_metadata
      context        = (future: session state, severity tier)

    Contact center heritage: this is the routing engine's permission
    check before connecting a caller to a resource. The agent (bot or
    human) has its own scope independent of the caller's.
    """
    permission = _policy_store.tool_permissions.get(tool_name)
    if permission is None:
        logger.warning(
            "Tool %s not registered (caller=%s, agent=%s)",
            tool_name, caller_id, agent_identity,
        )
        return AuthorizationResult(
            allowed=False,
            reason=f"Tool '{tool_name}' not registered",
            agent_identity=agent_identity,
        )

    if requested_scope not in permission.allowed_scopes:
        return AuthorizationResult(
            allowed=False,
            reason=f"Scope '{requested_scope}' not in allowed scopes for '{tool_name}'",
            agent_identity=agent_identity,
        )

    if permission.requires_human_approval:
        return AuthorizationResult(
            allowed=False,
            reason=f"Tool '{tool_name}' requires human approval (HITL gate)",
            agent_identity=agent_identity,
        )

    return AuthorizationResult(
        allowed=True,
        reason="Authorized",
        agent_identity=agent_identity,
    )


def authorize_retrieval(
    caller_role: str,
    corpus_id: str,
    agent_identity: str = "",
) -> AuthorizationResult:
    """Check whether a retrieval request is authorized for this corpus.

    ACL enforcement at the retrieval layer. A denied retrieval returns nothing,
    not a filtered subset. Fail-closed.

    Authorization input tuple for OPA migration:
      user_identity  = caller_role
      agent_identity = which agent is requesting (v1: engagement-agent-v1)
      resource       = corpus_id
      action         = "retrieve"
      arguments      = (future: query metadata)
      context        = (future: session state)
    """
    allowed = _policy_store.retrieval_scopes.get(caller_role, [])
    if corpus_id not in allowed:
        logger.info(
            "Retrieval denied: role=%s corpus=%s agent=%s",
            caller_role, corpus_id, agent_identity,
        )
        return AuthorizationResult(
            allowed=False,
            reason=f"Role '{caller_role}' not authorized for corpus '{corpus_id}'",
            agent_identity=agent_identity,
        )
    return AuthorizationResult(
        allowed=True,
        reason="Authorized",
        agent_identity=agent_identity,
    )
