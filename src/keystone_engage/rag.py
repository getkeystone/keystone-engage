"""Retrieval and generation for Keystone Engage.

Fail-closed at retrieval: if confidence is below threshold, the system refuses
rather than guessing. Connects to Ollama via OpenAI-compatible HTTP.
Falls back to stub mode if Ollama is unreachable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from openai import AsyncOpenAI

from keystone_engage.config import get_settings
from keystone_engage.vectorstore import InMemoryVectorStore, QueryResult

logger = logging.getLogger(__name__)

ENGAGE_SYSTEM_PROMPT = """You are a governed customer engagement agent. You help customers understand their options and take action on their accounts.

RULES:
1. Only use information from the PROVIDED CONTEXT below. Do not invent or assume facts.
2. If the context does not contain enough information to answer confidently, say "I don't have enough information to answer that confidently" and recommend the customer speak with a specialist.
3. Use empathetic, respectful language. Acknowledge the customer's situation before providing options.
4. Never make commitments or promises not explicitly stated in the context.
5. When referencing specific policies, programs, or limits, cite the source document name.
6. If the topic involves legal rights, regulatory requirements, or significant financial decisions, recommend the customer speak with a qualified professional.
7. Keep responses concise and actionable. Avoid lengthy preambles.

CONTEXT:
{context}

Respond to the customer's message based on the rules and context above."""


@dataclass
class RetrievalResult:
    chunk_id: str
    content: str
    source_document: str
    section: str
    similarity_score: float
    evidence_tier: str = "verified"


@dataclass
class RAGResponse:
    """Response from the RAG pipeline with full provenance."""

    answer: str
    retrieved_chunks: list[RetrievalResult]
    model_used: str
    confidence_score: float
    fail_closed: bool = False
    fail_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class EngageRAG:
    """RAG pipeline for Keystone Engage.

    When vectorstore is populated and Ollama is reachable, produces real
    governed responses. Otherwise falls back to fail-closed stub behavior.
    """

    def __init__(
        self,
        vectorstore: InMemoryVectorStore | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        settings = get_settings()
        self.vectorstore = vectorstore or InMemoryVectorStore()
        self.client = client or AsyncOpenAI(
            base_url=f"{settings.ollama_base_url}/v1",
            api_key="ollama",
        )
        self.chat_model = settings.ollama_chat_model
        self.embed_model = settings.ollama_embed_model
        self.top_k = settings.retrieval_top_k
        self.confidence_threshold = settings.confidence_threshold
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready and self.vectorstore.size > 0

    def mark_ready(self) -> None:
        self._ready = True

    async def embed(self, text: str) -> list[float]:
        """Embed a single text using Ollama's embedding model."""
        response = await self.client.embeddings.create(
            model=self.embed_model,
            input=text,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts sequentially."""
        embeddings = []
        for text in texts:
            emb = await self.embed(text)
            embeddings.append(emb)
        return embeddings

    async def retrieve_and_generate(
        self,
        query: str,
        corpus_id: str = "default",
        max_chunks: int | None = None,
    ) -> RAGResponse:
        """Retrieve relevant chunks and generate a governed response.

        Fail-closed: if the vectorstore is empty, Ollama is unreachable,
        or no chunks meet the confidence threshold, returns a fail-closed
        response. The orchestrator handles HITL routing.
        """
        top_k = max_chunks or self.top_k
        start_time = time.monotonic()

        if not self.ready:
            return RAGResponse(
                answer="",
                retrieved_chunks=[],
                model_used=self.chat_model,
                confidence_score=0.0,
                fail_closed=True,
                fail_reason="RAG pipeline not ready: corpus not loaded or inference unavailable",
            )

        try:
            query_embedding = await self.embed(query)
        except Exception as e:
            logger.error("Embedding failed: %s", e)
            return RAGResponse(
                answer="", retrieved_chunks=[], model_used=self.chat_model,
                confidence_score=0.0, fail_closed=True,
                fail_reason=f"Embedding failed: {e}",
            )

        results: list[QueryResult] = self.vectorstore.query(query_embedding, k=top_k)

        if not results:
            return RAGResponse(
                answer="", retrieved_chunks=[], model_used=self.chat_model,
                confidence_score=0.0, fail_closed=True, fail_reason="No chunks retrieved",
            )

        best_score = results[0].score
        retrieved = [
            RetrievalResult(
                chunk_id=r.chunk.chunk_id, content=r.chunk.content,
                source_document=r.chunk.source_document, section=r.chunk.section,
                similarity_score=r.score, evidence_tier=r.chunk.evidence_tier,
            )
            for r in results
        ]

        if best_score < self.confidence_threshold:
            elapsed = (time.monotonic() - start_time) * 1000
            return RAGResponse(
                answer="", retrieved_chunks=retrieved, model_used=self.chat_model,
                confidence_score=best_score, fail_closed=True,
                fail_reason=f"Best retrieval score {best_score:.3f} below threshold {self.confidence_threshold}",
                latency_ms=elapsed,
            )

        context_parts = []
        for r in results:
            context_parts.append(
                f"[Source: {r.chunk.source_document}, Section: {r.chunk.section}]\n{r.chunk.content}"
            )
        context = "\n\n---\n\n".join(context_parts)

        try:
            completion = await self.client.chat.completions.create(
                model=self.chat_model,
                messages=[
                    {"role": "system", "content": ENGAGE_SYSTEM_PROMPT.format(context=context)},
                    {"role": "user", "content": query},
                ],
                temperature=0.3,
            )
            answer = completion.choices[0].message.content or ""
            input_tokens = completion.usage.prompt_tokens if completion.usage else 0
            output_tokens = completion.usage.completion_tokens if completion.usage else 0
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            elapsed = (time.monotonic() - start_time) * 1000
            return RAGResponse(
                answer="", retrieved_chunks=retrieved, model_used=self.chat_model,
                confidence_score=best_score, fail_closed=True,
                fail_reason=f"LLM call failed: {e}", latency_ms=elapsed,
            )

        elapsed = (time.monotonic() - start_time) * 1000
        return RAGResponse(
            answer=answer, retrieved_chunks=retrieved, model_used=self.chat_model,
            confidence_score=best_score, input_tokens=input_tokens,
            output_tokens=output_tokens, latency_ms=elapsed,
        )
