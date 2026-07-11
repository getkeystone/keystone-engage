"""Pydantic types for Keystone Engage.

Frame-based dialog state carries forward the contact center dialog management
heritage: slots are structured (not free-form context), state transitions are
explicit, and each frame carries provenance for audit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --- Severity tiers (maps to bot-to-human escalation severity classification) ---


class SeverityTier(str, Enum):
    """HITL routing severity. Higher tiers require human review before response."""

    TIER_0 = "tier_0"  # Fully automated, no review
    TIER_1 = "tier_1"  # Automated with post-hoc review queue
    TIER_2 = "tier_2"  # Human review required before response
    TIER_3 = "tier_3"  # Immediate escalation, no automated response attempted


# --- Dialog frame (maps to frame-based dialog slot validation) ---


class FrameSlot(BaseModel):
    """A single slot in a dialog frame. Must be filled from a verified source."""

    name: str
    value: Any | None = None
    source: str | None = None  # provenance: which retrieval chunk or tool filled this
    verified: bool = False
    filled_at: datetime | None = None


class DialogFrame(BaseModel):
    """Frame-based dialog state. Cannot advance without required slots filled."""

    frame_id: str
    step: int = 0
    slots: list[FrameSlot] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def required_slots_filled(self) -> bool:
        return all(slot.verified for slot in self.slots)


# --- Audit entry (hash-chained, compatible with keystone-core) ---


class AuditEntry(BaseModel):
    """Hash-chained audit record. Append-only, tamper-evident.

    Maps to contact center compliance logging: every routing decision,
    disposition code, and disclosure event recorded in a tamper-evident trail.
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str
    actor: str  # system component or user identifier
    payload: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = ""
    curr_hash: str = ""

    def compute_hash(self, prev_hash: str) -> str:
        self.prev_hash = prev_hash
        content = json.dumps(
            {
                "timestamp": self.timestamp.isoformat(),
                "event_type": self.event_type,
                "actor": self.actor,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
        )
        self.curr_hash = hashlib.sha256(content.encode()).hexdigest()
        return self.curr_hash


# --- Tool authorization (hard architectural layer, not prompt-mediated) ---


class ToolPermission(BaseModel):
    """Scoped permission for a tool call. Authorization is structural, not heuristic."""

    tool_name: str
    allowed_scopes: list[str]
    requires_human_approval: bool = False
    reversible: bool = True


# --- API types ---


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    component: str = "keystone-engage"
    platform: str = "keystone"
    pipeline: str = "v1"


class EngageRequest(BaseModel):
    """Inbound request to the Engage agent."""

    session_id: str
    message: str
    caller_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EngageResponse(BaseModel):
    """Outbound response from the Engage agent."""

    session_id: str
    message: str
    severity: SeverityTier = SeverityTier.TIER_0
    frame: DialogFrame | None = None
    audit_hash: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)
