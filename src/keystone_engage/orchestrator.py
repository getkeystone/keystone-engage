"""Keystone Engage orchestrator.

The control-plane service that receives a request, checks authorization,
manages dialog frame state, dispatches to the inference plane for model calls,
records audit entries, and applies severity-tier HITL routing logic.
"""

from __future__ import annotations

import logging
import uuid

from keystone_engage.audit import AuditChain
from keystone_engage.auth import authorize_retrieval
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
    """Orchestrator for governed conversational engagement."""

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
        """Full orchestration loop: audit open, authz check, RAG call,
        evidence gate, severity routing, audit close with cost."""
        tracer = get_tracer()

        with agent_span(tracer, "engage-orchestrator", request.session_id):
            opening = self.audit.append(
                event_type="request.received",
                actor="orchestrator",
                payload={
                    "session_id": request.session_id,
                    "caller_id": request.caller_id or "anonymous",
                    "message_length": len(request.message),
                },
            )

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

            frame = self._get_or_create_frame(request.session_id)

            rag_response = await self.rag.retrieve_and_generate(
                query=request.message,
                corpus_id="engage-default",
            )

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
