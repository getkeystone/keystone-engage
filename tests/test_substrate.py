"""Tests for the day-one substrate package.

Covers four components:
  1. Substrate models (Agent, Task, AuditSubstrateFields, enums, constants)
  2. Authorization with agent_identity (auth.py)
  3. OTel substrate attributes (observability.py)
  4. Audit chain with substrate parameter (audit.py)

All tests run without database or Ollama.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# 1. Substrate models
# ---------------------------------------------------------------------------


class TestSubstrateModels:
    """Substrate models import, validate, and enforce constraints."""

    def test_v1_constants(self):
        from keystone_engage.substrate import (
            V1_DEFAULT_BUDGET_CENTS,
            V1_ENGAGEMENT_AGENT_ID,
            V1_ENGAGEMENT_AGENT_TEMPO,
        )

        assert V1_ENGAGEMENT_AGENT_ID == "engagement-agent-v1"
        assert V1_ENGAGEMENT_AGENT_TEMPO.value == "fast"
        assert V1_DEFAULT_BUDGET_CENTS == 100

    def test_agent_tempo_enum_values(self):
        from keystone_engage.substrate import AgentTempo

        assert set(t.value for t in AgentTempo) == {"fast", "medium", "slow", "deferred"}

    def test_task_state_enum_values(self):
        from keystone_engage.substrate import TaskState

        assert set(s.value for s in TaskState) == {"created", "in_progress", "completed", "failed"}

    def test_cost_profile_valid(self):
        from keystone_engage.substrate import CostProfile

        cp = CostProfile(
            typical_input_tokens=500,
            typical_output_tokens=300,
            typical_latency_ms=1000,
            model_used="qwen2.5:7b-instruct",
        )
        assert cp.typical_input_tokens == 500
        assert cp.model_used == "qwen2.5:7b-instruct"

    def test_cost_profile_rejects_negative_tokens(self):
        from keystone_engage.substrate import CostProfile

        with pytest.raises(ValidationError):
            CostProfile(
                typical_input_tokens=-1,
                typical_output_tokens=300,
                typical_latency_ms=1000,
                model_used="test",
            )

    def test_audit_substrate_fields_required(self):
        from keystone_engage.substrate import AgentTempo, AuditSubstrateFields

        fields = AuditSubstrateFields(
            agent_id="engagement-agent-v1",
            tempo=AgentTempo.FAST,
        )
        assert fields.agent_id == "engagement-agent-v1"
        assert fields.tempo == AgentTempo.FAST

    def test_audit_substrate_fields_optional_defaults(self):
        from keystone_engage.substrate import AgentTempo, AuditSubstrateFields

        fields = AuditSubstrateFields(
            agent_id="engagement-agent-v1",
            tempo=AgentTempo.FAST,
        )
        assert fields.task_id is None
        assert fields.input_tokens is None
        assert fields.output_tokens is None
        assert fields.model_used is None
        assert fields.cost_cents is None
        assert fields.latency_ms is None
        assert fields.session_rolling_cost_cents is None

    def test_audit_substrate_fields_with_cost(self):
        from keystone_engage.substrate import AgentTempo, AuditSubstrateFields

        task_id = uuid4()
        fields = AuditSubstrateFields(
            agent_id="engagement-agent-v1",
            tempo=AgentTempo.FAST,
            task_id=task_id,
            input_tokens=965,
            output_tokens=225,
            model_used="qwen2.5:7b-instruct",
            cost_cents=Decimal("0"),
            latency_ms=5329,
            session_rolling_cost_cents=Decimal("0"),
        )
        assert fields.task_id == task_id
        assert fields.input_tokens == 965
        assert fields.cost_cents == Decimal("0")


# ---------------------------------------------------------------------------
# 2. Authorization with agent_identity
# ---------------------------------------------------------------------------


class TestAuthAgentIdentity:
    """Authorization functions accept and propagate agent_identity."""

    def test_authorize_retrieval_with_agent_identity(self):
        from keystone_engage.auth import authorize_retrieval, get_policy_store

        store = get_policy_store()
        store.register_retrieval_scope("test-role", ["test-corpus"])

        result = authorize_retrieval(
            caller_role="test-role",
            corpus_id="test-corpus",
            agent_identity="engagement-agent-v1",
        )
        assert result.allowed
        assert result.agent_identity == "engagement-agent-v1"

    def test_authorize_retrieval_denied_with_agent_identity(self):
        from keystone_engage.auth import authorize_retrieval

        result = authorize_retrieval(
            caller_role="unknown-role",
            corpus_id="test-corpus",
            agent_identity="engagement-agent-v1",
        )
        assert not result.allowed
        assert result.agent_identity == "engagement-agent-v1"

    def test_authorize_retrieval_without_agent_identity(self):
        """Backward compatibility: agent_identity defaults to empty string."""
        from keystone_engage.auth import authorize_retrieval, get_policy_store

        store = get_policy_store()
        store.register_retrieval_scope("compat-role", ["compat-corpus"])

        result = authorize_retrieval(
            caller_role="compat-role",
            corpus_id="compat-corpus",
        )
        assert result.allowed
        assert result.agent_identity == ""

    def test_authorize_tool_call_with_agent_identity(self):
        from keystone_engage.auth import authorize_tool_call, get_policy_store
        from keystone_engage.models import ToolPermission

        store = get_policy_store()
        store.register_tool(ToolPermission(
            tool_name="test-tool",
            allowed_scopes=["read"],
            requires_human_approval=False,
        ))

        result = authorize_tool_call(
            tool_name="test-tool",
            caller_id="test-caller",
            requested_scope="read",
            agent_identity="engagement-agent-v1",
        )
        assert result.allowed
        assert result.agent_identity == "engagement-agent-v1"

    def test_authorize_tool_call_denied_propagates_agent_identity(self):
        from keystone_engage.auth import authorize_tool_call

        result = authorize_tool_call(
            tool_name="nonexistent-tool",
            caller_id="test-caller",
            requested_scope="read",
            agent_identity="engagement-agent-v1",
        )
        assert not result.allowed
        assert result.agent_identity == "engagement-agent-v1"


# ---------------------------------------------------------------------------
# 3. OTel substrate attributes
# ---------------------------------------------------------------------------


class TestOTelSubstrate:
    """record_substrate_attributes sets the correct span attributes."""

    def test_record_substrate_attributes_all_fields(self):
        from unittest.mock import MagicMock

        from keystone_engage.observability import record_substrate_attributes

        span = MagicMock()
        record_substrate_attributes(
            span,
            agent_id="engagement-agent-v1",
            agent_tempo="fast",
            task_id="abc-123",
            priority=0,
            cost_cents=0.0,
            budget_remaining_cents=100.0,
        )
        span.set_attribute.assert_any_call("keystone.agent_id", "engagement-agent-v1")
        span.set_attribute.assert_any_call("keystone.agent_tempo", "fast")
        span.set_attribute.assert_any_call("keystone.task_id", "abc-123")
        span.set_attribute.assert_any_call("keystone.priority", 0)
        span.set_attribute.assert_any_call("keystone.cost_cents", 0.0)
        span.set_attribute.assert_any_call("keystone.budget_remaining_cents", 100.0)
        assert span.set_attribute.call_count == 6

    def test_record_substrate_attributes_partial(self):
        """Only provided fields are set. None values are skipped."""
        from unittest.mock import MagicMock

        from keystone_engage.observability import record_substrate_attributes

        span = MagicMock()
        record_substrate_attributes(span, agent_id="engagement-agent-v1")
        span.set_attribute.assert_called_once_with("keystone.agent_id", "engagement-agent-v1")

    def test_record_substrate_attributes_none_skipped(self):
        """Calling with no arguments sets nothing."""
        from unittest.mock import MagicMock

        from keystone_engage.observability import record_substrate_attributes

        span = MagicMock()
        record_substrate_attributes(span)
        span.set_attribute.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Audit chain with substrate parameter
# ---------------------------------------------------------------------------


class TestAuditSubstrate:
    """AuditChain.append accepts optional substrate parameter."""

    def test_audit_chain_append_with_substrate(self, tmp_path):
        from keystone_engage.audit import AuditChain
        from keystone_engage.substrate import AgentTempo, AuditSubstrateFields

        chain = AuditChain(ledger_path=tmp_path / "test_ledger.jsonl")
        substrate = AuditSubstrateFields(
            agent_id="engagement-agent-v1",
            tempo=AgentTempo.FAST,
            input_tokens=500,
            output_tokens=200,
        )
        entry = chain.append(
            event_type="test.event",
            actor="test-actor",
            payload={"key": "value"},
            substrate=substrate,
        )
        assert entry.curr_hash != ""
        assert entry.event_type == "test.event"

    def test_audit_chain_append_without_substrate(self, tmp_path):
        """Backward compatibility: substrate parameter is optional."""
        from keystone_engage.audit import AuditChain

        chain = AuditChain(ledger_path=tmp_path / "test_ledger.jsonl")
        entry = chain.append(
            event_type="test.event",
            actor="test-actor",
            payload={"key": "value"},
        )
        assert entry.curr_hash != ""

    def test_audit_chain_integrity_with_substrate(self, tmp_path):
        """Chain integrity holds when substrate parameter is used."""
        from keystone_engage.audit import AuditChain
        from keystone_engage.substrate import AgentTempo, AuditSubstrateFields

        chain = AuditChain(ledger_path=tmp_path / "test_ledger.jsonl")
        substrate = AuditSubstrateFields(
            agent_id="engagement-agent-v1",
            tempo=AgentTempo.FAST,
        )

        chain.append("event.one", "actor", {"n": 1}, substrate=substrate)
        chain.append("event.two", "actor", {"n": 2}, substrate=substrate)
        chain.append("event.three", "actor", {"n": 3})

        valid, count, msg = chain.verify_chain()
        assert valid, msg
        assert count == 3
