"""In-memory vector store for Keystone Engage.

Pure Python cosine similarity over embeddings. Sufficient for corpora under
200 chunks. Swappable to pgvector on Data-Plane when the data plane is wired;
the query interface stays the same.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ChunkRecord:
    """A document chunk with metadata for provenance tracking."""

    chunk_id: str
    content: str
    source_document: str
    section: str
    evidence_tier: str = "verified"


@dataclass
class QueryResult:
    """A retrieval result with similarity score."""

    chunk: ChunkRecord
    score: float


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore:
    """Vector store backed by Python lists. No external dependencies.

    Interface matches what a pgvector-backed store would expose.
    When Data-Plane is ready, swap this implementation without changing callers.
    """

    def __init__(self) -> None:
        self._chunks: list[ChunkRecord] = []
        self._embeddings: list[list[float]] = []

    @property
    def size(self) -> int:
        return len(self._chunks)

    def add(self, chunk: ChunkRecord, embedding: list[float]) -> None:
        self._chunks.append(chunk)
        self._embeddings.append(embedding)

    def query(self, query_embedding: list[float], k: int = 5) -> list[QueryResult]:
        """Return top-k chunks by cosine similarity."""
        if not self._chunks:
            return []

        scored = [
            QueryResult(chunk=self._chunks[i], score=_cosine_similarity(query_embedding, emb))
            for i, emb in enumerate(self._embeddings)
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:k]

    def clear(self) -> None:
        self._chunks.clear()
        self._embeddings.clear()
