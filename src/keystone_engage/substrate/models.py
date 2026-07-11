"""
Day-one substrate models for keystone-engage.

Four dimensions across every audit entry, every OTel span, and every
dispatch call: agent identity, tempo, task state, cost.

v1 populates them with constants for one agent. v2 populates for many.

v2 task state machine expands the lifecycle for multi-agent operation:
  claimed_by_agent    : an agent accepted the task
  stuck               : no heartbeat received within threshold
  rescheduled         : takeover happened, new agent assigned
  completed_verified  : output verified by another agent
  failed_unrecoverable: retry exhausted or verification failed

Contact center heritage:
  agent identity = which routing engine handled the interaction
  tempo          = SLA class (IVR sub-second, human agent seconds, WFM hours)
  task state     = disposition machine (full lifecycle with transfers)
  cost           = per-interaction rate rolled up per session
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

    v1 minimal: created, in_progress, completed, failed.
    v2 multi-agent: adds claim, heartbeat-aware stuck detection,
    takeover via rescheduling, verification, and unrecoverable failure.

    Contact center heritage:
      created             = call entered queue
      claimed_by_agent    = agent accepted the call
      in_progress         = agent actively handling (with heartbeat)
      stuck               = agent went silent (no heartbeat)
      rescheduled         = call transferred to another agent
      completed           = agent finished, pending verification
      completed_verified  = supervisor reviewed and approved
      failed              = retriable failure
      failed_unrecoverable = dead letter, human review required
    """

    CREATED = "created"
    CLAIMED_BY_AGENT = "claimed_by_agent"
    IN_PROGRESS = "in_progress"
    STUCK = "stuck"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    COMPLETED_VERIFIED = "completed_verified"
    FAILED = "failed"
    FAILED_UNRECOVERABLE = "failed_unrecoverable"


# Valid state transitions. Key is current state, value is set of
# allowed next states. Any transition not in this map is rejected.
VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.CREATED: {
        TaskState.CLAIMED_BY_AGENT,
        TaskState.IN_PROGRESS,  # v1 compat: direct to in_progress
        TaskState.FAILED,
    },
    TaskState.CLAIMED_BY_AGENT: {
        TaskState.IN_PROGRESS,
        TaskState.FAILED,
    },
    TaskState.IN_PROGRESS: {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.STUCK,
    },
    TaskState.STUCK: {
        TaskState.RESCHEDULED,
        TaskState.FAILED_UNRECOVERABLE,
    },
    TaskState.RESCHEDULED: {
        TaskState.CLAIMED_BY_AGENT,
        TaskState.FAILED_UNRECOVERABLE,
    },
    TaskState.COMPLETED: {
        TaskState.COMPLETED_VERIFIED,
        TaskState.FAILED,  # verification rejected
    },
    TaskState.COMPLETED_VERIFIED: set(),  # terminal
    TaskState.FAILED: {
        TaskState.RESCHEDULED,  # retry via takeover
        TaskState.FAILED_UNRECOVERABLE,
    },
    TaskState.FAILED_UNRECOVERABLE: set(),  # terminal
}


class InvalidTransition(Exception):
    """Raised when a task state transition is not valid."""

    def __init__(self, task_id: UUID, current: TaskState, target: TaskState):
        self.task_id = task_id
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid transition for task {task_id}: "
            f"{current.value} -> {target.value}"
        )


def validate_transition(current: TaskState, target: TaskState) -> bool:
    """Check whether a state transition is valid."""
    allowed = VALID_TRANSITIONS.get(current, set())
    return target in allowed


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
    """A registered agent in the agent registry.

    v1 has one entry: engagement-agent-v1.
    v2 adds entries without schema change.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str
    agent_name: str
    agent_role: str
    tempo: AgentTempo
    cost_profile: CostProfile
    registered_at: datetime


class Task(BaseModel):
    """A dispatched task with v2 lifecycle fields."""

    task_id: UUID
    owner_agent_id: str
    state: TaskState
    payload: dict[str, Any]
    budget_cents: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    last_heartbeat_at: datetime | None = None
    heartbeat_interval_s: int = 30
    previous_owner_id: str | None = None
    takeover_count: int = 0
    stuck_reason: str | None = None


class AuditSubstrateFields(BaseModel):
    """Substrate fields added to audit_entries by migration 003.

    Populated on every audit write. In v1, agent_id and tempo are always
    the engagement agent constants. task_id and cost fields are populated
    when a model call occurs (a pure routing decision may omit cost).
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


# -------------------------------------------------------------------
# v1 constants
# -------------------------------------------------------------------

V1_ENGAGEMENT_AGENT_ID: str = "engagement-agent-v1"
V1_ENGAGEMENT_AGENT_TEMPO: AgentTempo = AgentTempo.FAST
V1_DEFAULT_BUDGET_CENTS: int = 100  # $1.00 per-turn default
V2_DEFAULT_HEARTBEAT_INTERVAL_S: int = 30
V2_STUCK_THRESHOLD_MULTIPLIER: float = 3.0  # stuck if no heartbeat for 3x interval
