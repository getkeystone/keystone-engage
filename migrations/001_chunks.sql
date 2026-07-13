-- Keystone Engage: chunk storage with pgvector
-- Run against keystone_engage database on Data-Plane

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    source_document TEXT NOT NULL,
    section TEXT NOT NULL,
    evidence_tier TEXT NOT NULL DEFAULT 'verified',
    embedding vector(768) NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Fast lookup by source document
CREATE INDEX IF NOT EXISTS idx_chunks_source
    ON chunks (source_document);
