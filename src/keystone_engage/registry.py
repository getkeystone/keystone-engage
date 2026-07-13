"""Agent registry for multi-agent Engage.

Defines specialist agent identities, capabilities, and routing metadata.
The coordinator queries this registry to decide which agents to invoke
for a given request.

v1: in-process registry (Python dict). Matches the agents table on
Data-Plane. When the agent table becomes the source of truth (v2),
this module becomes a cache layer with a refresh-from-DB path.

Agent roles:
  engagement : primary conversational agent (RAG pipeline)
  empathy    : distress detection and acknowledgment (pre-dispatch)
  escalation : severity routing and HITL handoff (pre-dispatch)
  budget     : cost tracking and enforcement (wraps dispatch)
  monitor    : post-dispatch quality and compliance check (async)

Contact center heritage:
  engagement = the agent handling the call
  empathy    = IVR distress screening before agent connect
  escalation = supervisor routing engine
  budget     = rate cap per interaction
  monitor    = QM real-time sampling
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from keystone_engage.substrate.models import AgentTempo

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Specialist agent roles."""

    ENGAGEMENT = "engagement"
    EMPATHY = "empathy"
    ESCALATION = "escalation"
    BUDGET = "budget"
    MONITOR = "monitor"


class DispatchPhase(str, Enum):
    """When in the pipeline this agent runs.

    PRE_DISPATCH  : before the primary agent (gates, screening)
    DISPATCH      : the primary agent itself
    WRAP_DISPATCH : wraps the primary agent call (budget enforcement)
    POST_DISPATCH : after the primary agent (monitoring, verification)
    """

    PRE_DISPATCH = "pre_dispatch"
    DISPATCH = "dispatch"
    WRAP_DISPATCH = "wrap_dispatch"
    POST_DISPATCH = "post_dispatch"


@dataclass(frozen=True)
class AgentSpec:
    """Specification for a registered agent."""

    agent_id: str
    agent_name: str
    role: AgentRole
    tempo: AgentTempo
    phase: DispatchPhase
    description: str = ""
    enabled: bool = True


# Registry: all specialist agents known to the system.
# Order matters within each phase: agents run in list order.
AGENT_REGISTRY: dict[str, AgentSpec] = {}


def _register(spec: AgentSpec) -> None:
    AGENT_REGISTRY[spec.agent_id] = spec


# --- Registration ---

_register(AgentSpec(
    agent_id="empathy-agent-v1",
    agent_name="Empathy Agent",
    role=AgentRole.EMPATHY,
    tempo=AgentTempo.FAST,
    phase=DispatchPhase.PRE_DISPATCH,
    description="Detects distress signals and provides empathetic acknowledgment before dispatch.",
))

_register(AgentSpec(
    agent_id="escalation-agent-v1",
    agent_name="Escalation Agent",
    role=AgentRole.ESCALATION,
    tempo=AgentTempo.FAST,
    phase=DispatchPhase.PRE_DISPATCH,
    description="Detects escalation triggers and routes to HITL at the appropriate severity tier.",
))

_register(AgentSpec(
    agent_id="engagement-agent-v1",
    agent_name="Engagement Agent",
    role=AgentRole.ENGAGEMENT,
    tempo=AgentTempo.FAST,
    phase=DispatchPhase.DISPATCH,
    description="Primary conversational agent. RAG pipeline for governed customer interaction.",
))

_register(AgentSpec(
    agent_id="budget-agent-v1",
    agent_name="Budget Agent",
    role=AgentRole.BUDGET,
    tempo=AgentTempo.FAST,
    phase=DispatchPhase.WRAP_DISPATCH,
    description="Monitors cost per dispatch. Can throttle or deny if budget exceeded.",
))

_register(AgentSpec(
    agent_id="monitor-agent-v1",
    agent_name="Role Monitor Agent",
    role=AgentRole.MONITOR,
    tempo=AgentTempo.DEFERRED,
    phase=DispatchPhase.POST_DISPATCH,
    description="Async quality and compliance check on completed interactions.",
))


def get_agents_by_phase(phase: DispatchPhase) -> list[AgentSpec]:
    """Return agents for a given phase, in registration order."""
    return [
        spec for spec in AGENT_REGISTRY.values()
        if spec.phase == phase and spec.enabled
    ]


def get_agent(agent_id: str) -> AgentSpec | None:
    """Look up an agent by ID."""
    return AGENT_REGISTRY.get(agent_id)


def get_pipeline_order() -> list[AgentSpec]:
    """Return all enabled agents in pipeline execution order.

    PRE_DISPATCH -> DISPATCH -> WRAP_DISPATCH -> POST_DISPATCH
    """
    phase_order = [
        DispatchPhase.PRE_DISPATCH,
        DispatchPhase.DISPATCH,
        DispatchPhase.WRAP_DISPATCH,
        DispatchPhase.POST_DISPATCH,
    ]
    result = []
    for phase in phase_order:
        result.extend(get_agents_by_phase(phase))
    return result
