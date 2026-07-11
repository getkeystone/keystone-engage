"""Tests for the agent registry."""

from keystone_engage.registry import (
    AGENT_REGISTRY,
    AgentRole,
    DispatchPhase,
    get_agent,
    get_agents_by_phase,
    get_pipeline_order,
)


class TestAgentRegistry:
    def test_five_agents_registered(self):
        assert len(AGENT_REGISTRY) == 5

    def test_all_agent_ids_present(self):
        expected = {
            "empathy-agent-v1",
            "escalation-agent-v1",
            "engagement-agent-v1",
            "budget-agent-v1",
            "monitor-agent-v1",
        }
        assert set(AGENT_REGISTRY.keys()) == expected

    def test_get_agent_by_id(self):
        spec = get_agent("empathy-agent-v1")
        assert spec is not None
        assert spec.role == AgentRole.EMPATHY
        assert spec.phase == DispatchPhase.PRE_DISPATCH

    def test_get_unknown_agent_returns_none(self):
        assert get_agent("nonexistent") is None


class TestPhaseGrouping:
    def test_pre_dispatch_agents(self):
        agents = get_agents_by_phase(DispatchPhase.PRE_DISPATCH)
        ids = [a.agent_id for a in agents]
        assert "empathy-agent-v1" in ids
        assert "escalation-agent-v1" in ids
        assert len(agents) == 2

    def test_dispatch_agent(self):
        agents = get_agents_by_phase(DispatchPhase.DISPATCH)
        assert len(agents) == 1
        assert agents[0].agent_id == "engagement-agent-v1"

    def test_wrap_dispatch_agent(self):
        agents = get_agents_by_phase(DispatchPhase.WRAP_DISPATCH)
        assert len(agents) == 1
        assert agents[0].agent_id == "budget-agent-v1"

    def test_post_dispatch_agent(self):
        agents = get_agents_by_phase(DispatchPhase.POST_DISPATCH)
        assert len(agents) == 1
        assert agents[0].agent_id == "monitor-agent-v1"


class TestPipelineOrder:
    def test_pipeline_has_all_five(self):
        pipeline = get_pipeline_order()
        assert len(pipeline) == 5

    def test_pipeline_phase_order(self):
        """Agents run in phase order: pre, dispatch, wrap, post."""
        pipeline = get_pipeline_order()
        phases = [a.phase for a in pipeline]
        # All pre_dispatch before dispatch
        pre_end = max(i for i, p in enumerate(phases) if p == DispatchPhase.PRE_DISPATCH)
        dispatch_idx = next(i for i, p in enumerate(phases) if p == DispatchPhase.DISPATCH)
        wrap_idx = next(i for i, p in enumerate(phases) if p == DispatchPhase.WRAP_DISPATCH)
        post_idx = next(i for i, p in enumerate(phases) if p == DispatchPhase.POST_DISPATCH)
        assert pre_end < dispatch_idx < wrap_idx < post_idx

    def test_empathy_before_escalation(self):
        """Empathy fires before escalation in pre-dispatch."""
        pipeline = get_pipeline_order()
        ids = [a.agent_id for a in pipeline]
        assert ids.index("empathy-agent-v1") < ids.index("escalation-agent-v1")
