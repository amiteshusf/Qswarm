"""Jira HTTP routes (stub mode in default test env)."""

from fastapi.testclient import TestClient

from app.main import app


def test_jira_connection_test_stub_mode():
    with TestClient(app) as client:
        r = client.get("/jira/connection-test")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["mode"] == "stub"
    assert "stub" in data["message"].lower() or "not tested" in data["message"].lower()


def test_jira_issues_returns_normalized_shape():
    with TestClient(app) as client:
        r = client.get("/jira/issues/NSP-677")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["issue_key"] == "NSP-677"
    assert "summary" in data
    assert "raw" not in data
