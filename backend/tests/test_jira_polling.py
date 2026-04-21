"""Lightweight checks for Jira polling JQL and wiring (see test_jira_pickup.py for behavior)."""

from app.services.jira_pickup_service import jira_pickup_jql


def test_pickup_jql_includes_label_and_issue_types():
    jql = jira_pickup_jql("qswarm-test-design")
    assert "qswarm-test-design" in jql
    assert "Story" in jql
    assert "Task" in jql
