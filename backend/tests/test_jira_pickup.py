"""Jira label pickup polling (no real Jira; dependency override + stub get_issue)."""

from __future__ import annotations

from typing import Any

import pytest

from app.api.deps import get_jira_client
from app.connectors.jira_client import JiraClient
from app.core.config import get_settings
from app.core.constants import WorkflowRunStatus
from app.main import app
from app.schemas.workflow import WorkflowRunCreateRequest
from app.services import workflow_service


@pytest.fixture(autouse=True)
def _clear_jira_override():
    yield
    app.dependency_overrides.pop(get_jira_client, None)


def _hit(
    *,
    key: str = "QSW-101",
    summary: str = "Implement resilient checkout happy path tests",
    labels: list[str] | None = None,
    issue_type: str = "Story",
    status_category_key: str | None = "indeterminate",
) -> dict[str, Any]:
    return {
        "issue_key": key,
        "summary": summary,
        "labels": labels if labels is not None else ["qswarm-test-design"],
        "issue_type": issue_type,
        "status_category_key": status_category_key,
        "status": "In Progress",
    }


class _HarnessJira:
    """Returns canned search rows; delegates get_issue to stub JiraClient for Sprint1."""

    def __init__(self, hits: list[dict[str, Any]], *, fail_search: bool = False):
        self._hits = hits
        self._fail_search = fail_search
        self._inner = JiraClient(get_settings())

    def search_issues(self, jql: str, max_results: int = 20):
        from app.connectors.jira_client import JiraClientError

        if self._fail_search:
            raise JiraClientError("search unavailable", status_code=503)
        return {"issues": self._hits[:max_results], "total": len(self._hits)}

    def get_issue(self, issue_key: str):
        return self._inner.get_issue(issue_key)


def test_poll_picks_up_eligible_story(client):
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira([_hit()])
    r = client.post("/jira/pickup/poll", json={"limit": 10})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["checked"] == 1
    assert data["picked_up"] == 1
    assert data["skipped"] == 0
    assert len(data["results"]) == 1
    row = data["results"][0]
    assert row["action"] == "picked_up"
    assert row["issue_key"] == "QSW-101"
    assert row.get("workflow_run_id")


def test_poll_skips_done_status_category(client):
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira(
        [_hit(status_category_key="done")]
    )
    r = client.post("/jira/pickup/poll", json={})
    data = r.json()
    assert data["picked_up"] == 0
    assert data["skipped"] == 1
    assert data["results"][0]["reason"] == "done_status_category"


def test_poll_skips_unsupported_issue_type(client):
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira(
        [_hit(issue_type="Bug")]
    )
    data = client.post("/jira/pickup/poll", json={}).json()
    assert data["skipped"] == 1
    assert data["results"][0]["reason"] == "unsupported_issue_type"


def test_poll_skips_duplicate_active_run(client, db_session):
    existing = workflow_service.create_run(
        db_session,
        WorkflowRunCreateRequest(jira_issue_key="QSW-101", initiated_by="other"),
    )
    db_session.commit()
    existing.status = WorkflowRunStatus.AWAITING_APPROVAL.value
    db_session.commit()

    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira([_hit()])
    data = client.post("/jira/pickup/poll", json={}).json()
    assert data["picked_up"] == 0
    assert data["skipped"] == 1
    assert data["results"][0]["reason"] == "duplicate_active_run"


def test_poll_allows_missing_description_fields(client):
    """Preflight uses search row only; empty description in Jira is not a hard rule."""
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira(
        [_hit(summary="Full summary for pickup without caring about description in Jira")]
    )
    data = client.post("/jira/pickup/poll", json={}).json()
    assert data["picked_up"] == 1
    assert data["results"][0]["action"] == "picked_up"


def test_poll_start_failure_recorded(client, monkeypatch):
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira([_hit()])

    def boom(*args, **kwargs):
        raise RuntimeError("simulated graph failure")

    monkeypatch.setattr("app.services.workflow_service.start_run", boom)

    data = client.post("/jira/pickup/poll", json={}).json()
    assert data["picked_up"] == 0
    assert data["skipped"] == 1
    assert data["results"][0]["reason"] == "workflow_start_failed"


def test_poll_response_counts_multiple(client):
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira(
        [
            _hit(key="QSW-1", summary="First story with enough text"),
            _hit(key="QSW-2", summary="Second story with enough text"),
            _hit(key="QSW-2", summary="duplicate key in payload should dedupe"),
            _hit(key="QSW-3", issue_type="Bug"),
        ]
    )
    data = client.post("/jira/pickup/poll", json={"limit": 10}).json()
    assert data["checked"] == 3
    assert data["picked_up"] == 2
    assert data["skipped"] == 1
    assert len(data["results"]) == 3


def test_poll_jira_search_error(client):
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira([], fail_search=True)
    data = client.post("/jira/pickup/poll", json={}).json()
    assert data["ok"] is False
    assert data["checked"] == 0
    assert data["results"][0]["action"] == "error"
    assert data["results"][0]["reason"] == "jira_fetch_error"


def test_poll_skips_too_vague_summary(client):
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira([_hit(summary="todo")])
    data = client.post("/jira/pickup/poll", json={}).json()
    assert data["skipped"] == 1
    assert data["results"][0]["reason"] == "too_vague"


def test_poll_skips_missing_label_on_row(client):
    app.dependency_overrides[get_jira_client] = lambda: _HarnessJira(
        [_hit(labels=["other-label"])]
    )
    data = client.post("/jira/pickup/poll", json={}).json()
    assert data["skipped"] == 1
    assert data["results"][0]["reason"] == "missing_label"
