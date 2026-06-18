"""Smoke tests for Keystone Engage."""

from fastapi.testclient import TestClient

from keystone_engage.api import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["component"] == "keystone-engage"
    assert data["platform"] == "keystone"
    assert "version" in data


def test_health_version_matches_package():
    from keystone_engage import __version__

    response = client.get("/health")
    data = response.json()
    assert data["version"] == __version__


def test_engage_endpoint_exists():
    """The /engage endpoint exists and accepts POST."""
    response = client.post(
        "/engage",
        json={
            "session_id": "test-session-001",
            "message": "Hello",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "test-session-001"
    assert "audit_hash" in data
    assert data["audit_hash"] != ""


def test_audit_chain_integrity_after_request():
    """Verify that the audit chain is intact after processing a request."""
    from keystone_engage.api import _orchestrator

    valid, count, message = _orchestrator.audit.verify_chain()
    assert valid, f"Audit chain broken: {message}"
    assert count > 0, "No audit entries after request"
