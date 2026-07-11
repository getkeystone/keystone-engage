"""Keystone Engage orchestrator.

The control-plane service. Pre-dispatch pipeline: escalation detection
then intent classification. Both bypass the dispatcher entirely when
triggered.

A2A readiness: the orchestrator calls self.dispatcher.dispatch() instead
of calling RAG directly. The dispatcher is a protocol: LocalDispatcher
for in-process RAG, A2ADispatcher for remote agents. The orchestrator
does not know which one it has.

Substrate threading: every audit entry carries agent_id, tempo, task_id,
and cost fields. v1 uses constants. v2 populates from the dispatch result.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from keystone_engage.audit import AuditChain
from keystone_engage.auth import authorize_retrieval
from keystone_engage.dispatch import (
    Dispatcher,
    DispatchRequest,
    LocalDispatcher,
)
from keystone_engage.escalation import check_escalation
from keystone_engage.intent import check_intent
from keystone_engage.models import (
    DialogFrame,
    EngageRequest,
    EngageResponse,
    SeverityTier,
)
from keystone_engage.observability import agent_span, get_tracer, llm_span, record_token_usage
from keystone_engage.substrate.models import (
    AgentTempo,
    AuditSubstrateFields,
    TaskState,
    V1_DEFAULT_BUDGET_CENTS,
    V1_ENGAGEMENT_AGENT_ID,
    V1_ENGAGEMENT_AGENT_TEMPO,
)
from keystone_engage.substrate.store import TaskStore

logger = logging.getLogger(__name__)


class EngageOrchestrator:
    """Orchestrator for governed conversational engagement.

    Pre-dispatch pipeline order:
    1. Task creation + audit open
    2. Authorization check (hard gate)
    3. Escalation detection (crisis, supervisor, legal, discrimination)
    4. Intent classification (creative, general knowledge, entertainment)
    5. Dispatch to agent (via Dispatcher protocol)
    6. Audit close with cost + task completion
    """

    def __init__(
        self,
        audit: AuditChain | None = None,
        dispatcher: Dispatcher | None = None,
        task_store: TaskStore | None = None,
    ) -> None:
        self.audit = audit or AuditChain()
        self.dispatcher = dispatcher
        self.task_store = task_store
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
        task_id: uuid.UUID | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        model_used: str | None = None,
        cost_cents: Decimal | None = None,
        latency_ms: int | None = None,
        session_rolling_cost_cents: Decimal | None = None,
    ) -> AuditSubstrateFields:
        return AuditSubstrateFields(
            agent_id=V1_ENGAGEMENT_AGENT_ID,
            tempo=V1_ENGAGEMENT_AGENT_TEMPO,
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
            owner_agent_id=V1_ENGAGEMENT_AGENT_ID,
            payload={"session_id": session_id, "message_preview": message[:200]},
            budget_cents=V1_DEFAULT_BUDGET_CENTS,
        )

    def _complete_task(self, task_id: uuid.UUID | None, state: TaskState) -> None:
        if self.task_store and task_id:
            self.task_store.update_state(task_id, state)

    async def handle(self, request: EngageRequest) -> EngageResponse:
        tracer = get_tracer()

        with agent_span(tracer, "engage-orchestrator", request.session_id):
            # 1. Task creation + audit open
            task_id = self._create_task(request.session_id, request.message)
            if self.task_store and task_id:
                self.task_store.update_state(task_id, TaskState.IN_PROGRESS)

            opening = self.audit.append(
                event_type="request.received",
                actor="orchestrator",
                payload={
                    "session_id": request.session_id,
                    "caller_id": request.caller_id or "anonymous",
                    "message_length": len(request.message),
                },
                substrate=self._make_substrate(
                    task_id=task_id,
                    session_rolling_cost_cents=self._get_rolling_cost(request.session_id),
                ),
            )

            # 2. Authorization check (fail-closed)
            authz = authorize_retrieval(
                caller_role=request.caller_id or "public",
                corpus_id="engage-default",
                agent_identity=V1_ENGAGEMENT_AGENT_ID,
            )
            if not authz.allowed:
                self.audit.append(
                    event_type="authorization.denied",
                    actor="orchestrator",
                    payload={
                        "session_id": request.session_id,
                        "reason": authz.reason,
                        "decision_source": authz.decision_source,
                    },
                    substrate=self._make_substrate(task_id=task_id),
                )
                self._complete_task(task_id, TaskState.COMPLETED)
                return EngageResponse(
                    session_id=request.session_id,
                    message="Request not authorized.",
                    severity=SeverityTier.TIER_3,
                    audit_hash=opening.curr_hash,
                )

            # 3. Escalation detection (bypasses dispatch entirely)
            escalation = check_escalation(request.message)
            if escalation.should_escalate:
                self.audit.append(
                    event_type="escalation.triggered",
                    actor="orchestrator",
                    payload={
                        "session_id": request.session_id,
                        "trigger": escalation.trigger.value if escalation.trigger else "unknown",
                        "reason": escalation.reason,
                    },
                    substrate=self._make_substrate(task_id=task_id),
                )
                severity = (
                    SeverityTier.TIER_3
                    if escalation.trigger and escalation.trigger.value == "crisis_signal"
                    else SeverityTier.TIER_2
                )
                self._complete_task(task_id, TaskState.COMPLETED)
                return EngageResponse(
                    session_id=request.session_id,
                    message=f"I understand. {escalation.reason} Let me connect you with the right person to help.",
                    severity=severity,
                    audit_hash=opening.curr_hash,
                )

            # 4. Intent classification (bypasses dispatch for off-topic)
            intent = check_intent(request.message)
            if intent.is_off_topic:
                self.audit.append(
                    event_type="intent.off_topic",
                    actor="orchestrator",
                    payload={
                        "session_id": request.session_id,
                        "reason": intent.reason,
                    },
                    substrate=self._make_substrate(task_id=task_id),
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

            # 5. Dialog frame state
            frame = self._get_or_create_frame(request.session_id)

            # 6. Dispatch to agent
            if self.dispatcher is None:
                self._complete_task(task_id, TaskState.FAILED)
                return EngageResponse(
                    session_id=request.session_id,
                    message="No dispatcher configured.",
                    severity=SeverityTier.TIER_2,
                    audit_hash=opening.curr_hash,
                )

            dispatch_req = DispatchRequest(
                agent_id=V1_ENGAGEMENT_AGENT_ID,
                tempo_expectation=V1_ENGAGEMENT_AGENT_TEMPO,
                priority=0,
                budget_cents=V1_DEFAULT_BUDGET_CENTS,
                task_id=task_id,
                payload={
                    "query": request.message,
                    "corpus_id": "engage-default",
                },
            )

            result = await self.dispatcher.dispatch(dispatch_req)

            if result.input_tokens > 0:
                with llm_span(tracer, result.model_used) as span:
                    record_token_usage(
                        span,
                        result.input_tokens,
                        result.output_tokens,
                    )
                    span.set_attribute("keystone.latency_ms", result.latency_ms)

            # Cost tracking
            rolling_cost = self._add_session_cost(
                request.session_id, result.cost_cents
            )

            # 7. Audit close with cost + task completion
            closing = self.audit.append(
                event_type="response.generated",
                actor="orchestrator",
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
                },
                substrate=self._make_substrate(
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

            return EngageResponse(
                session_id=request.session_id,
                message=result.answer,
                severity=result.severity,
                frame=frame,
                audit_hash=closing.curr_hash,
                evidence=result.evidence,
            )
