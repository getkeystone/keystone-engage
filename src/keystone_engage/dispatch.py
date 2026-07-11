"""A2A-ready dispatch interface for Keystone Engage.

Abstracts "send work to an agent" behind a protocol. The orchestrator
calls dispatcher.dispatch() without knowing whether the agent is
in-process (LocalDispatcher) or remote (A2ADispatcher, future).

DispatchRequest carries the four substrate dimensions:
  agent_id          : which agent should handle this
  tempo_expectation : how fast the caller expects a response
  priority          : 0=normal, 1=high, 2=urgent
  budget_cents      : maximum cost allowed for this dispatch

Contact center heritage: this is the ACD routing decision.
The dispatcher is the routing engine. The request is the call.
The agent is the resource. tempo_expectation is the SLA class.
priority is the queue priority. budget_cents is the rate cap.

v1: LocalDispatcher wraps the in-process RAG pipeline.
v2: A2ADispatcher sends HTTP requests to remote agents.
    The protocol is the same. Only the transport changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from keystone_engage.models import SeverityTier
from keystone_engage.rag import EngageRAG
from keystone_engage.substrate.models import (
    AgentTempo,
    V1_DEFAULT_BUDGET_CENTS,
    V1_ENGAGEMENT_AGENT_ID,
    V1_ENGAGEMENT_AGENT_TEMPO,
)

logger = logging.getLogger(__name__)


@dataclass
class DispatchRequest:
    """Work to be dispatched to an agent.

    The payload is agent-specific. For the engagement agent, it
    contains the query and corpus_id. For a future compliance agent,
    it might contain a document and a checklist.
    """

    agent_id: str = V1_ENGAGEMENT_AGENT_ID
    tempo_expectation: AgentTempo = V1_ENGAGEMENT_AGENT_TEMPO
    priority: int = 0  # 0=normal, 1=high, 2=urgent
    budget_cents: int = V1_DEFAULT_BUDGET_CENTS
    task_id: UUID | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchResult:
    """Result from an agent after dispatch.

    Carries the response plus cost and provenance metadata.
    The orchestrator uses this to build the API response and
    the audit entry.
    """

    agent_id: str
    answer: str
    severity: SeverityTier
    evidence: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model_used: str = ""
    latency_ms: float = 0.0
    cost_cents: Decimal = Decimal("0")
    confidence_score: float = 0.0
    fail_closed: bool = False
    fail_reason: str = ""


@runtime_checkable
class Dispatcher(Protocol):
    """Protocol for dispatching work to agents.

    v1: LocalDispatcher (in-process RAG)
    v2: A2ADispatcher (HTTP to remote agents)

    The orchestrator depends on this protocol, not on the
    concrete implementation. Swapping dispatch backends is a
    configuration change, not a code change.
    """

    async def dispatch(self, request: DispatchRequest) -> DispatchResult: ...


class LocalDispatcher:
    """In-process dispatch via the local RAG pipeline.

    This is the v1 dispatcher. It calls EngageRAG.retrieve_and_generate()
    directly. When multi-agent ships, this class handles the engagement
    agent while A2ADispatcher handles remote agents. The dispatcher
    registry routes based on agent_id.

    Contact center heritage: this is the local agent pool. The call
    does not leave the building.
    """

    def __init__(self, rag: EngageRAG) -> None:
        self._rag = rag

    async def dispatch(self, request: DispatchRequest) -> DispatchResult:
        query = request.payload.get("query", "")
        corpus_id = request.payload.get("corpus_id", "engage-default")

        rag_response = await self._rag.retrieve_and_generate(
            query=query,
            corpus_id=corpus_id,
        )

        if rag_response.fail_closed:
            severity = SeverityTier.TIER_2
            answer = (
                "Unable to provide a confident response. "
                "This has been routed for human review."
            )
        else:
            severity = SeverityTier.TIER_0
            answer = rag_response.answer

        evidence = [
            {
                "chunk_id": c.chunk_id,
                "source": c.source_document,
                "section": c.section,
                "score": c.similarity_score,
            }
            for c in rag_response.retrieved_chunks
        ]

        return DispatchResult(
            agent_id=request.agent_id,
            answer=answer,
            severity=severity,
            evidence=evidence,
            input_tokens=rag_response.input_tokens,
            output_tokens=rag_response.output_tokens,
            model_used=rag_response.model_used,
            latency_ms=rag_response.latency_ms,
            cost_cents=Decimal("0"),  # v1 local inference
            confidence_score=rag_response.confidence_score,
            fail_closed=rag_response.fail_closed,
            fail_reason=rag_response.fail_reason if rag_response.fail_closed else "",
        )
