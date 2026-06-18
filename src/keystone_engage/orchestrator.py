"""Keystone Engage orchestrator.

The control-plane service that receives a request, checks authorization,
manages dialog frame state, dispatches to the inference plane for model calls,
records audit entries, and applies severity-tier HITL routing logic.

This is the supervisor in the supervisor/orchestrator-worker topology.
Pure orchestration by deliberate choice: legibility over resilience.
The choreography option is a documented graduation path item (Stage 2.2).
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
from keystone_engage.observability import agent_span, get_tracer
from keystone_engage.rag import EngageRAG

logger = logging.getLogger(__name__)


class EngageOrchestrator:
    """Orchestrator for governed conversational engagement.

    Responsibilities:
    1. Receive request, write opening audit entry
    2. Check authorization (hard gate, not heuristic)
    3. Manage dialog frame state (structured slots, not free-form context)
    4. Dispatch to RAG pipeline (retrieval + inference)
    5. Apply evidence gating (per-step, not post-hoc)
    6. Route by severity tier (HITL when required)
    7. Write closing audit entry with full provenance
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
        """Handle an inbound engagement request.

        Full orchestration loop: audit open, authz check, RAG call,
        evidence gate, severity routing, audit close.
        """
        tracer = get_tracer()

        with agent_span(tracer, "engage-orchestrator", request.session_id):
            # 1. Audit: opening entry
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
                    },
                )
                return EngageResponse(
                    session_id=request.session_id,
                    message="Request not authorized.",
                    severity=SeverityTier.TIER_3,
                    audit_hash=opening.curr_hash,
                )

            # 3. Dialog frame state
            frame = self._get_or_create_frame(request.session_id)

            # 4. RAG pipeline (retrieval + generation)
            rag_response = await self.rag.retrieve_and_generate(
                query=request.message,
                corpus_id="engage-default",
            )

            # 5. Evidence gating + severity routing
            if rag_response.fail_closed:
                severity = SeverityTier.TIER_2
                response_message = (
                    "Unable to provide a confident response. "
                    "This has been routed for human review."
                )
            else:
                severity = SeverityTier.TIER_0
                response_message = rag_response.answer

            # 6. Audit: closing entry with provenance
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
                        "score": c.similarity_score,
                    }
                    for c in rag_response.retrieved_chunks
                ],
            )
