"""Multi-agent pipeline coordinator for Keystone Engage.

Runs specialist agents in pipeline phase order:
  PRE_DISPATCH  : empathy screening, escalation detection
  DISPATCH      : primary engagement agent via dispatcher
  WRAP_DISPATCH : budget enforcement
  POST_DISPATCH : async quality monitoring via NATS

Each agent step is audited with its own agent_id in the substrate
fields. Event bus emissions allow external monitors and dashboards
to observe the pipeline in real time.

The v1 orchestrator (single-agent path) remains as fallback when
multi_agent=False or when the event bus is unavailable.

Contact center heritage: this is the call flow engine. The call
enters the IVR (pre-dispatch), routes to an agent (dispatch),
has its rate checked (wrap), and gets sampled by QM (post).
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from keystone_engage.audit import AuditChain
from keystone_engage.auth import authorize_retrieval
from keystone_engage.dispatch import Dispatcher, DispatchRequest
from keystone_engage.escalation import check_escalation
from keystone_engage.eventbus import EventBus
from keystone_engage.intent import check_intent
from keystone_engage.models import (
    DialogFrame,
    EngageRequest,
    EngageResponse,
    SeverityTier,
)
from keystone_engage.observability import agent_span, get_tracer, llm_span, record_token_usage
from keystone_engage.substrate.models import (
    AuditSubstrateFields,
    TaskState,
    V1_DEFAULT_BUDGET_CENTS,
    V1_ENGAGEMENT_AGENT_ID,
    V1_ENGAGEMENT_AGENT_TEMPO,
)
from keystone_engage.substrate.store import TaskStore

logger = logging.getLogger(__name__)

# Agent IDs for audit trails
_EMPATHY = "empathy-agent-v1"
_ESCALATION = "escalation-agent-v1"
_ENGAGEMENT = "engagement-agent-v1"
_BUDGET = "budget-agent-v1"
_MONITOR = "monitor-agent-v1"


class Coordinator:
    """Multi-agent pipeline coordinator.

    Runs five specialist agents in phase order. Any pre-dispatch agent
    can short-circuit the pipeline. The budget agent wraps the primary
    dispatch. The monitor agent fires asynchronously.

    The coordinator owns task lifecycle: create, state transitions,
    heartbeat, and completion. Each agent step produces an audit entry
    tagged with that agent's identity.
    """

    def __init__(
        self,
        audit: AuditChain,
        dispatcher: Dispatcher,
        task_store: TaskStore | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.audit = audit
        self.dispatcher = dispatcher
        self.task_store = task_store
        self.event_bus = event_bus
        self.sessions: dict[str, DialogFrame] = {}
        self._session_costs: dict[str, Decimal] = {}

    def _get_or_create_frame(self, session_id: str) -> DialogFrame:
        if session_id not in self.sessions:
            self.sessions[session_id] = DialogFrame(
                frame_id=f"frame-{uuid.uuid4().hex[:8]}",
            )
        return self.sessions[session_id]

    def _get_rolling_cost(self, session_id: str) -> Decimal:
        return self._session_costs.get(session_id, Decimal("0"))

    def _add_session_cost(self, session_id: str, cost_cents: Decimal) -> Decimal:
        current = self._session_costs.get(session_id, Decimal("0"))
        updated = current + cost_cents
        self._session_costs[session_id] = updated
        return updated

    def _make_substrate(
        self,
        agent_id: str,
        task_id: uuid.UUID | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        model_used: str | None = None,
        cost_cents: Decimal | None = None,
        latency_ms: int | None = None,
        session_rolling_cost_cents: Decimal | None = None,
    ) -> AuditSubstrateFields:
        from keystone_engage.registry import get_agent, AgentRole
        spec = get_agent(agent_id)
        tempo = spec.tempo if spec else V1_ENGAGEMENT_AGENT_TEMPO
        return AuditSubstrateFields(
            agent_id=agent_id,
            tempo=tempo,
            task_id=task_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_used=model_used,
            cost_cents=cost_cents,
            latency_ms=latency_ms,
            session_rolling_cost_cents=session_rolling_cost_cents,
        )

    def _create_task(self, session_id: str, message: str) -> uuid.UUID | None:
        if not self.task_store:
            return None
        return self.task_store.create_task(
            owner_agent_id=_ENGAGEMENT,
            payload={"session_id": session_id, "message_preview": message[:200]},
            budget_cents=V1_DEFAULT_BUDGET_CENTS,
        )

    def _complete_task(self, task_id: uuid.UUID | None, state: TaskState) -> None:
        if self.task_store and task_id:
            self.task_store.update_state(task_id, state)

    async def _emit(self, task_id: uuid.UUID | None, state: str, agent_id: str, payload: dict | None = None) -> None:
        """Publish a task lifecycle event to the event bus if connected."""
        if self.event_bus and self.event_bus.connected and task_id:
            try:
                await self.event_bus.publish_task_event(
                    task_id=str(task_id),
                    state=state,
                    agent_id=agent_id,
                    payload=payload,
                )
            except Exception as e:
                logger.warning("EventBus publish failed: %s", e)

    # ---------------------------------------------------------------
    # Pipeline
    # ---------------------------------------------------------------

    async def handle(self, request: EngageRequest) -> EngageResponse:
        tracer = get_tracer()

        with agent_span(tracer, "coordinator", request.session_id):
            # 1. Task creation
            task_id = self._create_task(request.session_id, request.message)
            if self.task_store and task_id:
                self.task_store.update_state(task_id, TaskState.IN_PROGRESS)
            await self._emit(task_id, "created", _ENGAGEMENT)

            opening = self.audit.append(
                event_type="request.received",
                actor="coordinator",
                payload={
                    "session_id": request.session_id,
                    "caller_id": request.caller_id or "anonymous",
                    "message_length": len(request.message),
                    "pipeline": "multi-agent",
                },
                substrate=self._make_substrate(
                    agent_id=_ENGAGEMENT,
                    task_id=task_id,
                    session_rolling_cost_cents=self._get_rolling_cost(request.session_id),
                ),
            )

            # 2. Authorization (not an agent, structural gate)
            authz = authorize_retrieval(
                caller_role=request.caller_id or "public",
                corpus_id="engage-default",
                agent_identity=_ENGAGEMENT,
            )
            if not authz.allowed:
                self.audit.append(
                    event_type="authorization.denied",
                    actor="coordinator",
                    payload={
                        "session_id": request.session_id,
                        "reason": authz.reason,
                    },
                    substrate=self._make_substrate(agent_id=_ENGAGEMENT, task_id=task_id),
                )
                self._complete_task(task_id, TaskState.COMPLETED)
                return EngageResponse(
                    session_id=request.session_id,
                    message="Request not authorized.",
                    severity=SeverityTier.TIER_3,
                    audit_hash=opening.curr_hash,
                )

            # 3. PRE_DISPATCH: empathy agent
            empathy_result = await self._run_empathy(request, task_id)
            if empathy_result is not None:
                self._complete_task(task_id, TaskState.COMPLETED)
                return empathy_result

            # 4. PRE_DISPATCH: escalation agent
            escalation_result = await self._run_escalation(request, task_id)
            if escalation_result is not None:
                self._complete_task(task_id, TaskState.COMPLETED)
                return escalation_result

            # 5. Intent check (inline, not yet a specialist agent)
            intent = check_intent(request.message)
            if intent.is_off_topic:
                self.audit.append(
                    event_type="intent.off_topic",
                    actor="coordinator",
                    payload={
                        "session_id": request.session_id,
                        "reason": intent.reason,
                    },
                    substrate=self._make_substrate(agent_id=_ENGAGEMENT, task_id=task_id),
                )
                self._complete_task(task_id, TaskState.COMPLETED)
                return EngageResponse(
                    session_id=request.session_id,
                    message=(
                        "I can only help with account-related questions such as "
                        "payment arrangements, hardship programs, and account inquiries. "
                        "How can I assist you with your account today?"
                    ),
                    severity=SeverityTier.TIER_2,
                    audit_hash=opening.curr_hash,
                )

            # 6. WRAP_DISPATCH: budget agent pre-check
            budget_ok = await self._run_budget_precheck(request, task_id)
            if not budget_ok:
                self._complete_task(task_id, TaskState.FAILED)
                return EngageResponse(
                    session_id=request.session_id,
                    message="Session budget exceeded. Please contact support for assistance.",
                    severity=SeverityTier.TIER_2,
                    audit_hash=opening.curr_hash,
                )

            # 7. DISPATCH: engagement agent
            frame = self._get_or_create_frame(request.session_id)

            dispatch_req = DispatchRequest(
                agent_id=_ENGAGEMENT,
                tempo_expectation=V1_ENGAGEMENT_AGENT_TEMPO,
                priority=0,
                budget_cents=V1_DEFAULT_BUDGET_CENTS,
                task_id=task_id,
                payload={
                    "query": request.message,
                    "corpus_id": "engage-default",
                },
            )

            await self._emit(task_id, "claimed_by_agent", _ENGAGEMENT)
            result = await self.dispatcher.dispatch(dispatch_req)
            await self._emit(task_id, "completed", _ENGAGEMENT, {
                "input_tokens": result.input_tokens,
                "model_used": result.model_used,
            })

            if result.input_tokens > 0:
                with llm_span(tracer, result.model_used) as span:
                    record_token_usage(span, result.input_tokens, result.output_tokens)
                    span.set_attribute("keystone.latency_ms", result.latency_ms)

            # 8. WRAP_DISPATCH: budget agent post-check (record cost)
            rolling_cost = self._add_session_cost(request.session_id, result.cost_cents)
            await self._run_budget_record(request, task_id, result.cost_cents, rolling_cost)

            # 9. Audit close
            closing = self.audit.append(
                event_type="response.generated",
                actor="coordinator",
                payload={
                    "session_id": request.session_id,
                    "severity": result.severity.value,
                    "model_used": result.model_used,
                    "confidence": result.confidence_score,
                    "fail_closed": result.fail_closed,
                    "chunk_count": len(result.evidence),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "latency_ms": round(result.latency_ms, 1),
                    "pipeline": "multi-agent",
                },
                substrate=self._make_substrate(
                    agent_id=_ENGAGEMENT,
                    task_id=task_id,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    model_used=result.model_used,
                    cost_cents=result.cost_cents,
                    latency_ms=round(result.latency_ms),
                    session_rolling_cost_cents=rolling_cost,
                ),
            )

            self._complete_task(task_id, TaskState.COMPLETED)

            # 10. POST_DISPATCH: monitor agent (async via NATS)
            await self._run_monitor(request, task_id, result, closing)

            return EngageResponse(
                session_id=request.session_id,
                message=result.answer,
                severity=result.severity,
                frame=frame,
                audit_hash=closing.curr_hash,
                evidence=result.evidence,
            )

    # ---------------------------------------------------------------
    # Specialist agent steps
    # ---------------------------------------------------------------

    async def _run_empathy(
        self, request: EngageRequest, task_id: uuid.UUID | None,
    ) -> EngageResponse | None:
        """PRE_DISPATCH: empathy agent checks for distress signals.

        If distress is detected, the empathy agent provides an
        acknowledgment and the pipeline short-circuits.
        Returns None to continue, EngageResponse to short-circuit.
        """
        from keystone_engage.empathy import check_empathy

        empathy = check_empathy(request.message)
        if not empathy.is_distress:
            return None

        self.audit.append(
            event_type="empathy.triggered",
            actor=_EMPATHY,
            payload={
                "session_id": request.session_id,
                "reason": empathy.reason,
            },
            substrate=self._make_substrate(agent_id=_EMPATHY, task_id=task_id),
        )
        await self._emit(task_id, "completed", _EMPATHY, {"reason": empathy.reason})

        if empathy.response:
            return EngageResponse(
                session_id=request.session_id,
                message=empathy.response,
                severity=SeverityTier.TIER_0,
                audit_hash="",
            )
        return None

    async def _run_escalation(
        self, request: EngageRequest, task_id: uuid.UUID | None,
    ) -> EngageResponse | None:
        """PRE_DISPATCH: escalation agent checks for escalation triggers.

        If escalation is needed, routes to HITL and short-circuits.
        Returns None to continue, EngageResponse to short-circuit.
        """
        escalation = check_escalation(request.message)
        if not escalation.should_escalate:
            return None

        severity = (
            SeverityTier.TIER_3
            if escalation.trigger and escalation.trigger.value == "crisis_signal"
            else SeverityTier.TIER_2
        )

        self.audit.append(
            event_type="escalation.triggered",
            actor=_ESCALATION,
            payload={
                "session_id": request.session_id,
                "trigger": escalation.trigger.value if escalation.trigger else "unknown",
                "reason": escalation.reason,
            },
            substrate=self._make_substrate(agent_id=_ESCALATION, task_id=task_id),
        )
        await self._emit(task_id, "completed", _ESCALATION, {
            "trigger": escalation.trigger.value if escalation.trigger else "unknown",
        })

        return EngageResponse(
            session_id=request.session_id,
            message=f"I understand. {escalation.reason} Let me connect you with the right person to help.",
            severity=severity,
            audit_hash="",
        )

    async def _run_budget_precheck(
        self, request: EngageRequest, task_id: uuid.UUID | None,
    ) -> bool:
        """WRAP_DISPATCH: budget agent checks if session has budget remaining.

        Returns True to continue, False to deny.
        """
        rolling = self._get_rolling_cost(request.session_id)
        budget_limit = Decimal(str(V1_DEFAULT_BUDGET_CENTS * 10))  # 10 turns at $1

        if rolling >= budget_limit:
            self.audit.append(
                event_type="budget.exceeded",
                actor=_BUDGET,
                payload={
                    "session_id": request.session_id,
                    "rolling_cost_cents": float(rolling),
                    "budget_limit_cents": float(budget_limit),
                },
                substrate=self._make_substrate(agent_id=_BUDGET, task_id=task_id),
            )
            await self._emit(task_id, "failed", _BUDGET, {
                "reason": "budget_exceeded",
                "rolling_cost_cents": float(rolling),
            })
            return False

        self.audit.append(
            event_type="budget.approved",
            actor=_BUDGET,
            payload={
                "session_id": request.session_id,
                "rolling_cost_cents": float(rolling),
                "budget_remaining_cents": float(budget_limit - rolling),
            },
            substrate=self._make_substrate(agent_id=_BUDGET, task_id=task_id),
        )
        return True

    async def _run_budget_record(
        self,
        request: EngageRequest,
        task_id: uuid.UUID | None,
        cost_cents: Decimal,
        rolling_cost: Decimal,
    ) -> None:
        """WRAP_DISPATCH: budget agent records actual cost after dispatch."""
        self.audit.append(
            event_type="budget.recorded",
            actor=_BUDGET,
            payload={
                "session_id": request.session_id,
                "dispatch_cost_cents": float(cost_cents),
                "rolling_cost_cents": float(rolling_cost),
            },
            substrate=self._make_substrate(
                agent_id=_BUDGET,
                task_id=task_id,
                cost_cents=cost_cents,
                session_rolling_cost_cents=rolling_cost,
            ),
        )

    async def _run_monitor(
        self,
        request: EngageRequest,
        task_id: uuid.UUID | None,
        result,
        closing,
    ) -> None:
        """POST_DISPATCH: monitor agent fires async quality check via NATS.

        The monitor agent does not block the response. It publishes an
        event that a separate monitor service can subscribe to for
        offline quality review.
        """
        self.audit.append(
            event_type="monitor.dispatched",
            actor=_MONITOR,
            payload={
                "session_id": request.session_id,
                "severity": result.severity.value,
                "confidence": result.confidence_score,
                "input_tokens": result.input_tokens,
                "audit_hash": closing.curr_hash,
            },
            substrate=self._make_substrate(agent_id=_MONITOR, task_id=task_id),
        )
        await self._emit(task_id, "completed", _MONITOR, {
            "action": "quality_check_queued",
            "severity": result.severity.value,
            "confidence": result.confidence_score,
        })
