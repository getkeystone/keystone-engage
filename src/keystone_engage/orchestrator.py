"""Keystone Engage orchestrator.

The control-plane service. Pre-RAG pipeline: escalation detection then
intent classification. Both bypass the LLM entirely when triggered.
"""

from __future__ import annotations

import logging
import uuid

from keystone_engage.audit import AuditChain
from keystone_engage.auth import authorize_retrieval
from keystone_engage.escalation import check_escalation
from keystone_engage.intent import check_intent
from keystone_engage.models import (
    DialogFrame,
    EngageRequest,
    EngageResponse,
    SeverityTier,
)
from keystone_engage.observability import agent_span, get_tracer, llm_span, record_token_usage
from keystone_engage.rag import EngageRAG

logger = logging.getLogger(__name__)


class EngageOrchestrator:
    """Orchestrator for governed conversational engagement.

    Pre-RAG pipeline order:
    1. Audit open
    2. Authorization check (hard gate)
    3. Escalation detection (crisis, supervisor, legal, discrimination)
    4. Intent classification (creative, general knowledge, entertainment)
    5. RAG retrieval + generation
    6. Evidence gating + severity routing
    7. Audit close with cost
    """

    def __init__(
        self,
        audit: AuditChain | None = None,
        rag: EngageRAG | None = None,
    ) -> None:
        self.audit = audit or AuditChain()
        self.rag = rag or EngageRAG()
        self.sessions: dict[str, DialogFrame] = {}

    def _get_or_create_frame(self, session_id: str) -> DialogFrame:
        if session_id not in self.sessions:
            self.sessions[session_id] = DialogFrame(
                frame_id=f"frame-{uuid.uuid4().hex[:8]}",
            )
        return self.sessions[session_id]

    async def handle(self, request: EngageRequest) -> EngageResponse:
        tracer = get_tracer()

        with agent_span(tracer, "engage-orchestrator", request.session_id):
            # 1. Audit open
            opening = self.audit.append(
                event_type="request.received",
                actor="orchestrator",
                payload={
                    "session_id": request.session_id,
                    "caller_id": request.caller_id or "anonymous",
                    "message_length": len(request.message),
                },
            )

            # 2. Authorization check (fail-closed)
            authz = authorize_retrieval(
                caller_role=request.caller_id or "public",
                corpus_id="engage-default",
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
                )
                return EngageResponse(
                    session_id=request.session_id,
                    message="Request not authorized.",
                    severity=SeverityTier.TIER_3,
                    audit_hash=opening.curr_hash,
                )

            # 3. Escalation detection (bypasses RAG entirely)
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
                )
                severity = (
                    SeverityTier.TIER_3
                    if escalation.trigger and escalation.trigger.value == "crisis_signal"
                    else SeverityTier.TIER_2
                )
                return EngageResponse(
                    session_id=request.session_id,
                    message=f"I understand. {escalation.reason} Let me connect you with the right person to help.",
                    severity=severity,
                    audit_hash=opening.curr_hash,
                )

            # 4. Intent classification (bypasses RAG for off-topic)
            intent = check_intent(request.message)
            if intent.is_off_topic:
                self.audit.append(
                    event_type="intent.off_topic",
                    actor="orchestrator",
                    payload={
                        "session_id": request.session_id,
                        "reason": intent.reason,
                    },
                )
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

            # 6. RAG pipeline
            rag_response = await self.rag.retrieve_and_generate(
                query=request.message,
                corpus_id="engage-default",
            )

            # 7. Evidence gating + severity routing
            if rag_response.fail_closed:
                severity = SeverityTier.TIER_2
                response_message = (
                    "Unable to provide a confident response. "
                    "This has been routed for human review."
                )
            else:
                severity = SeverityTier.TIER_0
                response_message = rag_response.answer

            if rag_response.input_tokens > 0:
                with llm_span(tracer, rag_response.model_used) as span:
                    record_token_usage(
                        span,
                        rag_response.input_tokens,
                        rag_response.output_tokens,
                    )
                    span.set_attribute("keystone.latency_ms", rag_response.latency_ms)

            # 8. Audit close with cost
            closing = self.audit.append(
                event_type="response.generated",
                actor="orchestrator",
                payload={
                    "session_id": request.session_id,
                    "severity": severity.value,
                    "model_used": rag_response.model_used,
                    "confidence": rag_response.confidence_score,
                    "fail_closed": rag_response.fail_closed,
                    "chunk_count": len(rag_response.retrieved_chunks),
                    "input_tokens": rag_response.input_tokens,
                    "output_tokens": rag_response.output_tokens,
                    "latency_ms": round(rag_response.latency_ms, 1),
                },
            )

            return EngageResponse(
                session_id=request.session_id,
                message=response_message,
                severity=severity,
                frame=frame,
                audit_hash=closing.curr_hash,
                evidence=[
                    {
                        "chunk_id": c.chunk_id,
                        "source": c.source_document,
                        "section": c.section,
                        "score": c.similarity_score,
                    }
                    for c in rag_response.retrieved_chunks
                ],
            )
