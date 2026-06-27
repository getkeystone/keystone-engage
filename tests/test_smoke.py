"""Tests for Keystone Engage.

Smoke tests run without Ollama (RAG operates in stub mode).
Unit tests verify corpus loading and vectorstore independently.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_audit_ledger():
    """Remove audit ledger before each test."""
    ledger = Path("data/audit/ledger.jsonl")
    if ledger.exists():
        ledger.unlink()
    yield


@pytest.fixture
def client():
    from keystone_engage.api import app
    with TestClient(app) as c:
        yield c


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["component"] == "keystone-engage"
    assert data["platform"] == "keystone"


def test_health_version_matches_package(client):
    from keystone_engage import __version__
    response = client.get("/health")
    assert response.json()["version"] == __version__


def test_engage_returns_response(client):
    response = client.post(
        "/engage",
        json={"session_id": "test-001", "message": "What payment options do I have?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "test-001"
    assert data["audit_hash"] != ""
    assert data["severity"] in ("tier_0", "tier_2")


def test_engage_with_caller_id(client):
    response = client.post(
        "/engage",
        json={"session_id": "test-002", "message": "Help", "caller_id": "public"},
    )
    assert response.status_code == 200


def test_audit_chain_integrity(client):
    client.post("/engage", json={"session_id": "test-audit", "message": "test"})
    from keystone_engage.api import _orchestrator
    assert _orchestrator is not None
    valid, count, message = _orchestrator.audit.verify_chain()
    assert valid, f"Audit chain broken: {message}"
    assert count >= 2


def test_corpus_loading():
    from keystone_engage.corpus import load_corpus
    chunks = load_corpus("data/corpus")
    assert len(chunks) > 0
    sources = {c.source_document for c in chunks}
    assert len(sources) >= 3


def test_corpus_chunk_format():
    from keystone_engage.corpus import load_corpus
    chunks = load_corpus("data/corpus")
    for chunk in chunks:
        assert "Document:" in chunk.content
        assert "Section:" in chunk.content


def test_vectorstore_add_and_query():
    from keystone_engage.vectorstore import ChunkRecord, InMemoryVectorStore
    store = InMemoryVectorStore()
    store.add(
        ChunkRecord(chunk_id="t1", content="Payment plans", source_document="t.md", section="Pay"),
        [1.0, 0.0, 0.0],
    )
    store.add(
        ChunkRecord(chunk_id="t2", content="Hardship help", source_document="t.md", section="Hard"),
        [0.0, 1.0, 0.0],
    )
    assert store.size == 2
    results = store.query([0.9, 0.1, 0.0], k=2)
    assert results[0].chunk.chunk_id == "t1"
    assert results[0].score > results[1].score


def test_vectorstore_empty():
    from keystone_engage.vectorstore import InMemoryVectorStore
    assert InMemoryVectorStore().query([1.0], k=5) == []


def test_audit_chain_standalone():
    from keystone_engage.audit import AuditChain
    chain = AuditChain(ledger_path="data/audit/test_ledger.jsonl")
    e1 = chain.append("test.event", "actor", {"k": "v1"})
    e2 = chain.append("test.event", "actor", {"k": "v2"})
    assert e2.prev_hash == e1.curr_hash
    valid, count, msg = chain.verify_chain()
    assert valid, msg
    assert count == 2
    Path("data/audit/test_ledger.jsonl").unlink(missing_ok=True)
