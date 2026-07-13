"""FastAPI application for Keystone Engage.

On startup: register auth scopes, choose vectorstore and audit backend
based on config, load corpus, index embeddings, wire substrate stores.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from keystone_engage import __version__
from keystone_engage.audit import AuditChain
from keystone_engage.auth import get_policy_store
from keystone_engage.config import get_settings
from keystone_engage.corpus import load_corpus
from keystone_engage.models import EngageRequest, EngageResponse, HealthResponse
from keystone_engage.observability import setup_telemetry
from keystone_engage.dispatch import LocalDispatcher
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
    settings = get_settings()
    if settings.database_url:
        try:
            from keystone_engage.pgvectorstore import PgVectorStore
            store = PgVectorStore(settings.database_url)
            logger.info("Using PgVectorStore on Data-Plane")
            return store
        except Exception as e:
            logger.warning("PgVectorStore failed (%s), falling back to in-memory", e)
    logger.info("Using InMemoryVectorStore")
    return InMemoryVectorStore()


def _create_audit():
    settings = get_settings()
    if settings.database_url:
        try:
            from keystone_engage.pgaudit import PgAuditChain
            audit = PgAuditChain(settings.database_url)
            logger.info("Using PgAuditChain on Data-Plane")
            return audit
        except Exception as e:
            logger.warning("PgAuditChain failed (%s), falling back to JSONL", e)
    logger.info("Using JSONL AuditChain")
    return AuditChain()


def _create_task_store():
    """Create TaskStore if database is available.

    Returns None if database_url is empty or connection fails.
    The orchestrator operates without task persistence in that case.
    """
    settings = get_settings()
    if settings.database_url:
        try:
            from keystone_engage.substrate.store import TaskStore
            store = TaskStore(settings.database_url)
            logger.info("Using TaskStore on Data-Plane")
            return store
        except Exception as e:
            logger.warning("TaskStore failed (%s), tasks will not be persisted", e)
    return None


async def _load_and_index_corpus(rag: EngageRAG, store_is_pg: bool) -> None:
    settings = get_settings()
    chunks = load_corpus(settings.corpus_dir)

    if not chunks:
        logger.warning("No corpus chunks loaded. RAG will operate in stub mode.")
        return

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

    audit = _create_audit()
    task_store = _create_task_store()

    rag = EngageRAG(vectorstore=vectorstore)
    await _load_and_index_corpus(rag, store_is_pg)
    dispatcher = LocalDispatcher(rag=rag)
    _orchestrator = EngageOrchestrator(audit=audit, dispatcher=dispatcher, task_store=task_store)

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


# --- CORS (local lab / operator surface access) -------------------------------
# Default: an explicit allow-list of localhost origins so the private Platform
# Lab (served over http://localhost) can READ responses from a browser —
# cross-origin browser POSTs otherwise fail preflight. This is deliberately
# NOT permissive: no wildcard and no file:// (null) origin by default.
#
# Opt-in knobs (local demo only):
#   KEYSTONE_CORS_ORIGINS="http://localhost:5173,..."  → replace the allow-list
#   KEYSTONE_CORS_ORIGINS="*"                          → wildcard (discouraged)
#   KEYSTONE_CORS_ALLOW_FILE="1"                       → also allow "null" (file://)
#
# Auth is Bearer-token in the Authorization header (no cookies), so
# allow_credentials stays False.
import os
from fastapi.middleware.cors import CORSMiddleware

_cors_env = os.environ.get("KEYSTONE_CORS_ORIGINS", "").strip()
if _cors_env == "*":
    _cors_origins = ["*"]
elif _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    _cors_origins = [
        "http://localhost:8082", "http://127.0.0.1:8082",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://localhost:5500", "http://127.0.0.1:5500",
    ]
# file:// (null) origin is opt-in only — keep it out of the default policy.
if os.environ.get("KEYSTONE_CORS_ALLOW_FILE", "").strip() in ("1", "true", "True") \
        and "*" not in _cors_origins and "null" not in _cors_origins:
    _cors_origins = _cors_origins + ["null"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@app.post("/engage", response_model=EngageResponse)
async def engage(request: EngageRequest) -> EngageResponse:
    assert _orchestrator is not None, "Orchestrator not initialized"
    return await _orchestrator.handle(request)
