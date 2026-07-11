"""Tests for the multi-agent coordinator.

Tests pipeline flow, specialist agent short-circuits, budget enforcement,
and audit trail correctness. Uses mocks for dispatcher and event bus.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from keystone_engage.audit import AuditChain
from keystone_engage.auth import get_policy_store
from keystone_engage.coordinator import Coordinator
from keystone_engage.dispatch import DispatchResult
from keystone_engage.models import EngageRequest, SeverityTier


def _make_request(message: str, session_id: str = "test-session") -> EngageRequest:
    return EngageRequest(session_id=session_id, message=message)


def _make_dispatch_result(**overrides) -> DispatchResult:
    defaults = dict(
        agent_id="engagement-agent-v1",
        answer="Here is the answer.",
        severity=SeverityTier.TIER_0,
        evidence=[],
        input_tokens=100,
        output_tokens=50,
        model_used="qwen2.5:7b-instruct",
        latency_ms=500.0,
        cost_cents=Decimal("0"),
        confidence_score=0.8,
        fail_closed=False,
    )
    defaults.update(overrides)
    return DispatchResult(**defaults)


@pytest.fixture(autouse=True)
def _register_policies():
    store = get_policy_store()
    store.register_retrieval_scope("public", ["engage-default"])
    store.register_retrieval_scope("anonymous", ["engage-default"])

@pytest.fixture
def audit(tmp_path):
    return AuditChain(ledger_path=tmp_path / "test.jsonl")


@pytest.fixture
def mock_dispatcher():
    d = AsyncMock()
    d.dispatch = AsyncMock(return_value=_make_dispatch_result())
    return d


@pytest.fixture
def mock_event_bus():
    bus = MagicMock()
    bus.connected = True
    bus.publish_task_event = AsyncMock()
    return bus


@pytest.fixture
def mock_task_store():
    store = MagicMock()
    store.create_task = MagicMock(return_value=uuid.uuid4())
    store.update_state = MagicMock()
    return store

@pytest.fixture
def coordinator(audit, mock_dispatcher, mock_event_bus, mock_task_store):
    return Coordinator(
        audit=audit,
        dispatcher=mock_dispatcher,
        event_bus=mock_event_bus,
        task_store=mock_task_store,
    )


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_normal_request_reaches_dispatcher(self, coordinator, mock_dispatcher):
        request = _make_request("What payment plans are available?")
        response = await coordinator.handle(request)

        assert response.severity == SeverityTier.TIER_0
        assert len(response.message) > 0
        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_trail_includes_coordinator(self, coordinator, audit):
        request = _make_request("What payment plans are available?")
        await coordinator.handle(request)

        valid, count, msg = audit.verify_chain()
        assert valid
        assert count >= 3  # request.received, budget.approved, budget.recorded, response.generated, monitor


class TestEmpathyShortCircuit:
    @pytest.mark.asyncio
    async def test_distress_short_circuits(self, coordinator, mock_dispatcher):
        """Mock check_empathy to test coordinator routing, not pattern matching."""
        from keystone_engage.empathy import EmpathyResult
        mock_result = EmpathyResult(is_distress=True, reason="test distress", response="I hear you.")
        with patch("keystone_engage.empathy.check_empathy", return_value=mock_result):
            request = _make_request("any message")
            response = await coordinator.handle(request)
            assert response.severity == SeverityTier.TIER_0
            assert response.message == "I hear you."
            mock_dispatcher.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_distress_continues(self, coordinator, mock_dispatcher):
        """Non-distress messages pass through empathy to dispatcher."""
        request = _make_request("What are my payment options?")
        await coordinator.handle(request)
        mock_dispatcher.dispatch.assert_called_once()


class TestEscalationShortCircuit:
    @pytest.mark.asyncio
    async def test_crisis_signal_short_circuits(self, coordinator, mock_dispatcher):
        request = _make_request("I want to kill myself")
        response = await coordinator.handle(request)

        assert response.severity in (SeverityTier.TIER_2, SeverityTier.TIER_3)
        mock_dispatcher.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_supervisor_request_short_circuits(self, coordinator, mock_dispatcher):
        from keystone_engage.escalation import EscalationResult, EscalationTrigger
        mock_esc = EscalationResult(should_escalate=True, trigger=EscalationTrigger.SUPERVISOR_REQUEST, reason="Supervisor requested")
        with patch("keystone_engage.coordinator.check_escalation", return_value=mock_esc):
            request = _make_request("Let me talk to your supervisor")
            response = await coordinator.handle(request)
            assert response.severity in (SeverityTier.TIER_2, SeverityTier.TIER_3)
            mock_dispatcher.dispatch.assert_not_called()


class TestBudgetEnforcement:
    @pytest.mark.asyncio
    async def test_within_budget_proceeds(self, coordinator, mock_dispatcher):
        request = _make_request("What payment plans are available?")
        response = await coordinator.handle(request)

        assert response.severity == SeverityTier.TIER_0
        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_exceeded_budget_denies(self, coordinator, mock_dispatcher):
        # Burn the budget
        coordinator._session_costs["budget-session"] = Decimal("1500")

        request = _make_request("Another question", session_id="budget-session")
        response = await coordinator.handle(request)

        assert response.severity == SeverityTier.TIER_2
        assert "budget" in response.message.lower()
        mock_dispatcher.dispatch.assert_not_called()


class TestEventBusEmission:
    @pytest.mark.asyncio
    async def test_events_emitted_on_happy_path(self, coordinator, mock_event_bus):
        request = _make_request("What payment plans are available?")
        await coordinator.handle(request)

        # Should have emitted: created, claimed, completed (engagement), completed (monitor)
        assert mock_event_bus.publish_task_event.call_count >= 3

    @pytest.mark.asyncio
    async def test_no_crash_without_event_bus(self, audit, mock_dispatcher):
        coordinator = Coordinator(
            audit=audit,
            dispatcher=mock_dispatcher,
            event_bus=None,
        )
        request = _make_request("What payment plans are available?")
        response = await coordinator.handle(request)
        assert response.severity == SeverityTier.TIER_0


class TestMultiAgentAuditTrail:
    @pytest.mark.asyncio
    async def test_multiple_agent_ids_in_audit(self, coordinator, audit):
        request = _make_request("What payment plans are available?")
        await coordinator.handle(request)

        # Read audit entries
        import json
        entries = []
        with open(audit.ledger_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))

        actors = [e["actor"] for e in entries]
        # Should see coordinator and budget-agent-v1 at minimum
        assert "coordinator" in actors
        assert "budget-agent-v1" in actors
