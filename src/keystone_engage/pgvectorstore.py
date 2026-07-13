"""PostgreSQL + pgvector store for Keystone Engage.

Same interface as InMemoryVectorStore. Embeddings persist across restarts.
Connects to Data-Plane (data plane) for chunk storage and similarity search.
Falls back to InMemoryVectorStore if database is unavailable.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from keystone_engage.vectorstore import ChunkRecord, QueryResult

logger = logging.getLogger(__name__)


class PgVectorStore:
    """pgvector-backed store on Data-Plane. Same interface as InMemoryVectorStore."""

    def __init__(self, database_url: str, embedding_dim: int = 768) -> None:
        self._database_url = database_url
        self._embedding_dim = embedding_dim
        self._ensure_table()

    @contextmanager
    def _conn(self) -> Generator:
        conn = psycopg2.connect(self._database_url)
        try:
            register_vector(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_table(self) -> None:
        """Create chunks table if it does not exist."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS chunks (
                        chunk_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        source_document TEXT NOT NULL,
                        section TEXT NOT NULL,
                        evidence_tier TEXT NOT NULL DEFAULT 'verified',
                        embedding vector({self._embedding_dim}) NOT NULL,
                        indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chunks_embedding
                        ON chunks USING hnsw (embedding vector_cosine_ops)
                        WITH (m = 16, ef_construction = 64)
                """)
        logger.info("PgVectorStore: table verified on Data-Plane")

    @property
    def size(self) -> int:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM chunks")
                return cur.fetchone()[0]

    def add(self, chunk: ChunkRecord, embedding: list[float]) -> None:
        """Insert or update a chunk. Idempotent by chunk_id."""
        import numpy as np
        vec = np.array(embedding, dtype=np.float32)

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO chunks (chunk_id, content, source_document, section, evidence_tier, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (chunk_id) DO UPDATE SET
                           content = EXCLUDED.content,
                           embedding = EXCLUDED.embedding,
                           indexed_at = now()""",
                    (chunk.chunk_id, chunk.content, chunk.source_document,
                     chunk.section, chunk.evidence_tier, vec),
                )

    def query(self, query_embedding: list[float], k: int = 5) -> list[QueryResult]:
        """Return top-k chunks by cosine similarity via pgvector."""
        import numpy as np
        vec = np.array(query_embedding, dtype=np.float32)

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT chunk_id, content, source_document, section, evidence_tier,
                              1 - (embedding <=> %s) AS similarity
                       FROM chunks
                       ORDER BY embedding <=> %s
                       LIMIT %s""",
                    (vec, vec, k),
                )
                rows = cur.fetchall()

        return [
            QueryResult(
                chunk=ChunkRecord(
                    chunk_id=row[0], content=row[1], source_document=row[2],
                    section=row[3], evidence_tier=row[4],
                ),
                score=float(row[5]),
            )
            for row in rows
        ]

    def clear(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE chunks")
        logger.info("PgVectorStore: cleared all chunks")

    def has_source(self, source_document: str) -> bool:
        """Check if chunks from a source document are already indexed."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM chunks WHERE source_document = %s)",
                    (source_document,),
                )
                return cur.fetchone()[0]
