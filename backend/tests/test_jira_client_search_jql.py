"""Jira connector: enhanced JQL search path and response normalization."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.connectors.jira_client as jira_mod
from app.connectors.jira_client import JiraClient, JiraClientError


@pytest.fixture
def real_jira_settings():
    """Minimal settings object so ``effective_jira_stub`` is false."""
    return SimpleNamespace(
        effective_jira_stub=False,
        jira_base_url="https://usfoods.atlassian.net",
        jira_email="svc@example.com",
        jira_api_token="test-token",
    )


def test_search_issues_posts_to_search_jql_path(real_jira_settings, monkeypatch):
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "isLast": True,
                "issues": [
                    {
                        "id": "12345",
                        "key": "NSP-678",
                        "fields": {
                            "summary": "Labeled pickup story",
                            "labels": ["qswarm-test-design"],
                            "issuetype": {"name": "Story"},
                            "status": {
                                "name": "In Progress",
                                "statusCategory": {"key": "indeterminate"},
                            },
                        },
                    }
                ],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["body"] = json
            return FakeResponse()

    monkeypatch.setattr(jira_mod.httpx, "Client", FakeClient)

    client = JiraClient(real_jira_settings)
    out = client.search_issues('labels = "qswarm-test-design"', max_results=5)

    assert captured["url"] == "https://usfoods.atlassian.net/rest/api/3/search/jql"
    assert captured["body"]["jql"] == 'labels = "qswarm-test-design"'
    assert captured["body"]["maxResults"] == 5
    assert "summary" in captured["body"]["fields"]

    assert len(out["issues"]) == 1
    row = out["issues"][0]
    assert row["issue_key"] == "NSP-678"
    assert row["summary"] == "Labeled pickup story"
    assert row["labels"] == ["qswarm-test-design"]
    assert row["issue_type"] == "Story"
    assert row["status"] == "In Progress"
    assert row["status_category_key"] == "indeterminate"
    assert out["total"] == 1


def test_search_issues_maps_total_issue_count_when_total_missing(
    real_jira_settings, monkeypatch
):
    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "issues": [
                    {
                        "key": "A-1",
                        "fields": {
                            "summary": "One",
                            "labels": [],
                            "issuetype": {"name": "Task"},
                            "status": {"name": "Open", "statusCategory": {"key": "new"}},
                        },
                    }
                ],
                "totalIssueCount": 42,
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr(jira_mod.httpx, "Client", FakeClient)
    out = JiraClient(real_jira_settings).search_issues("project = NSP", max_results=10)
    assert out["total"] == 42


def test_search_issues_error_includes_path_and_status(real_jira_settings, monkeypatch):
    class FakeResponse:
        status_code = 410
        text = json.dumps(
            {
                "errorMessages": [
                    "The requested API has been removed. Please migrate to the /rest/api/3/search/jql API."
                ]
            }
        )

        def json(self):
            return json.loads(self.text)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr(jira_mod.httpx, "Client", FakeClient)

    with pytest.raises(JiraClientError) as ei:
        JiraClient(real_jira_settings).search_issues("project = X", max_results=1)
    assert ei.value.status_code == 410
    assert "/rest/api/3/search/jql" in str(ei.value)
    assert "410" in str(ei.value)
    assert "migrate" in str(ei.value).lower() or "removed" in str(ei.value).lower()
