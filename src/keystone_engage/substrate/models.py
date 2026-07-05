"""
Day-one substrate models for keystone-engage.

The substrate carries four dimensions across every audit entry, every OTel
span, and every dispatch call: agent identity, tempo, task state, cost.
v1 populates them with constants for one agent. v2 populates them for many.

Contact center heritage: this is the schema shape a multi-engine contact
center would have had if it had been designed as one platform instead of
federated engines.

  - Agent identity is which routing engine handled the interaction.
  - Tempo is the SLA class (IVR sub-second, human agent seconds, WFM minutes+).
  - Task state is the disposition machine.
  - Cost is the per-interaction rate rolled up per session.

The schema supports many agents. v1 populates one.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentTempo(str, Enum):
    """How fast an agent operates.

    fast     : sub-second (customer-facing engagement)
    medium   : seconds (compliance gating, evidence check)
    slow     : minutes (deferred analysis, batch scoring)
    deferred : hours or longer (offline evaluation, corpus refresh)
    """

    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"
    DEFERRED = "deferred"


class TaskState(str, Enum):
    """Task lifecycle state.

    v1 uses the minimal machine. v2 expands with claimed_by_agent,
    in_progress_with_heartbeat, stuck, rescheduled, completed_verified,
    failed_unrecoverable. See keystone-future-architecture Concept 6.
    """

    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class CostProfile(BaseModel):
    """Typical resource use for an agent.

    Populated from measured eval runs. Used later by cost-aware dispatch
    to predict cost before running. In v1 the profile is measured for
    engagement-agent-v1 and used for reporting only.
    """

    model_config = ConfigDict(frozen=True)

    typical_input_tokens: int = Field(ge=0)
    typical_output_tokens: int = Field(ge=0)
    typical_latency_ms: int = Field(ge=0)
    model_used: str


class Agent(BaseModel):
    """A registered agent. v1 has one: engagement-agent-v1."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    agent_name: str
    agent_role: str
    tempo: AgentTempo
    cost_profile: CostProfile
    registered_at: datetime


class Task(BaseModel):
    """A dispatched task. Every dispatch produces a row in the tasks table."""

    task_id: UUID
    owner_agent_id: str
    state: TaskState
    payload: dict[str, Any]
    budget_cents: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class AuditSubstrateFields(BaseModel):
    """Substrate fields added to audit_entries in migration 002.

    Populated on every audit write. agent_id and tempo are required.
    task_id and cost fields are populated when applicable (a model call
    has cost fields; a pure routing decision does not).
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str
    tempo: AgentTempo
    task_id: Optional[UUID] = None
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    model_used: Optional[str] = None
    cost_cents: Optional[Decimal] = Field(default=None, ge=0)
    latency_ms: Optional[int] = Field(default=None, ge=0)
    session_rolling_cost_cents: Optional[Decimal] = Field(default=None, ge=0)


# v1 sentinel constants. When v2 registers more agents these become
# lookup calls against the agents table. Keeping them here now means
# v1 call sites already speak the language of multi-agent dispatch.
V1_ENGAGEMENT_AGENT_ID: str = "engagement-agent-v1"
V1_ENGAGEMENT_AGENT_TEMPO: AgentTempo = AgentTempo.FAST
V1_DEFAULT_BUDGET_CENTS: int = 100  # $1.00 per-turn default