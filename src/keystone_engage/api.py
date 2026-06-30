"""FastAPI application for Keystone Engage.

On startup, loads behavioral content corpus and indexes embeddings.
Uses pgvector on AnchorNode if DATABASE_URL is configured, otherwise
falls back to in-memory vector store. Skips re-embedding if chunks
are already indexed in pgvector.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from keystone_engage import __version__
from keystone_engage.auth import get_policy_store
from keystone_engage.config import get_settings
from keystone_engage.corpus import load_corpus
from keystone_engage.models import EngageRequest, EngageResponse, HealthResponse
from keystone_engage.observability import setup_telemetry
from keystone_engage.orchestrator import EngageOrchestrator
from keystone_engage.rag import EngageRAG
from keystone_engage.vectorstore import InMemoryVectorStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_orchestrator: EngageOrchestrator | None = None


def _register_default_policies() -> None:
    store = get_policy_store()
    store.register_retrieval_scope("public", ["engage-default"])
    store.register_retrieval_scope("anonymous", ["engage-default"])
    logger.info("Registered default retrieval scopes")


def _create_vectorstore():
    """Create pgvector store if DATABASE_URL is set, otherwise in-memory."""
    settings = get_settings()
    if settings.database_url:
        try:
            from keystone_engage.pgvectorstore import PgVectorStore
            store = PgVectorStore(settings.database_url)
            logger.info("Using PgVectorStore on AnchorNode")
            return store
        except Exception as e:
            logger.warning("PgVectorStore failed (%s), falling back to in-memory", e)
    logger.info("Using InMemoryVectorStore")
    return InMemoryVectorStore()


async def _load_and_index_corpus(rag: EngageRAG, store_is_pg: bool) -> None:
    settings = get_settings()
    chunks = load_corpus(settings.corpus_dir)

    if not chunks:
        logger.warning("No corpus chunks loaded. RAG will operate in stub mode.")
        return

    # If pgvector and chunks already indexed, skip embedding
    if store_is_pg and rag.vectorstore.size > 0:
        logger.info(
            "PgVectorStore already has %d chunks. Skipping re-embedding.",
            rag.vectorstore.size,
        )
        rag.mark_ready()
        return

    logger.info("Embedding %d chunks (this may take a moment)...", len(chunks))
    try:
        texts = [c.content for c in chunks]
        embeddings = await rag.embed_batch(texts)

        for chunk, embedding in zip(chunks, embeddings):
            rag.vectorstore.add(chunk, embedding)

        rag.mark_ready()
        logger.info(
            "Corpus indexed: %d chunks in vectorstore. RAG is ready.",
            rag.vectorstore.size,
        )
    except Exception as e:
        logger.warning(
            "Failed to embed corpus (Ollama unreachable?): %s. "
            "RAG will operate in stub mode.",
            e,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    _register_default_policies()

    vectorstore = _create_vectorstore()
    store_is_pg = not isinstance(vectorstore, InMemoryVectorStore)

    rag = EngageRAG(vectorstore=vectorstore)
    await _load_and_index_corpus(rag, store_is_pg)
    _orchestrator = EngageOrchestrator(rag=rag)

    logger.info("Keystone Engage v%s ready", __version__)
    yield
    logger.info("Keystone Engage shutting down")


app = FastAPI(
    title="Keystone Engage",
    description=(
        "Governed conversational agent for regulated customer interaction. "
        "Part of the Keystone Applied Intelligence platform."
    ),
    version=__version__,
    lifespan=lifespan,
)

_tracer = setup_telemetry(app)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@app.post("/engage", response_model=EngageResponse)
async def engage(request: EngageRequest) -> EngageResponse:
    assert _orchestrator is not None, "Orchestrator not initialized"
    return await _orchestrator.handle(request)
