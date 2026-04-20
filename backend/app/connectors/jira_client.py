"""Read-only Jira REST client (v3) with httpx; stub mode for local development."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.core.config import Settings


class JiraClientError(Exception):
    """Raised when Jira returns an error or the request fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _extract_adf_text(node: Any) -> str:
    """Best-effort plain text from Atlassian Document Format (description)."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        parts: list[str] = []
        if node.get("type") == "text" and "text" in node:
            parts.append(str(node["text"]))
        for child in node.get("content") or []:
            parts.append(_extract_adf_text(child))
        return "\n".join(p for p in parts if p).strip()
    if isinstance(node, list):
        return "\n".join(_extract_adf_text(x) for x in node).strip()
    return ""


def _normalize_issue(issue_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    fields = payload.get("fields") or {}
    desc = fields.get("description")
    description_text = _extract_adf_text(desc) if isinstance(desc, dict) else (desc or "")

    assignee = fields.get("assignee") or {}
    reporter = fields.get("reporter") or {}
    issuetype = fields.get("issuetype") or {}
    priority = fields.get("priority") or {}
    status = fields.get("status") or {}

    return {
        "issue_key": issue_key.upper(),
        "issue_id": str(payload.get("id") or "") or None,
        "summary": str(fields.get("summary") or ""),
        "description": description_text or None,
        "issue_type": issuetype.get("name"),
        "priority": priority.get("name"),
        "status": status.get("name"),
        "assignee": assignee.get("displayName") or assignee.get("emailAddress"),
        "reporter": reporter.get("displayName") or reporter.get("emailAddress"),
        "labels": list(fields.get("labels") or []),
        "raw_payload": payload,
    }


def _stub_issue(issue_key: str) -> dict[str, Any]:
    key = issue_key.upper()
    return {
        "issue_key": key,
        "issue_id": "stub-1001",
        "summary": f"[Stub] Implement workflow for {key}",
        "description": (
            f"Stub Jira issue {key} for local development.\n"
            "As a QA engineer, I need the platform to ingest this story, "
            "produce a draft test design, and pause for human approval."
        ),
        "issue_type": "Story",
        "priority": "Medium",
        "status": "To Do",
        "assignee": "Unassigned",
        "reporter": "stub.user@example.com",
        "labels": ["stub", "sprint1"],
        "raw_payload": {"stub": True, "key": key},
    }


def _stub_search(jql: str, max_results: int) -> dict[str, Any]:
    return {
        "issues": [
            {
                "key": "STUB-1",
                "fields": {
                    "summary": f"Stub result for JQL: {jql[:40]}...",
                    "status": {"name": "Open"},
                },
            }
        ],
        "total": 1,
    }


class JiraClient:
    """Read-only Jira API access."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._base = settings.jira_base_url.rstrip("/") if settings.jira_base_url else ""
        self._stub = settings.effective_jira_stub

    def _auth_header(self) -> str:
        raw = f"{self._settings.jira_email}:{self._settings.jira_api_token}"
        return "Basic " + base64.b64encode(raw.encode()).decode()

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        """
        Fetch a single issue by key. Returns normalized dict with raw_payload included
        under key 'raw_payload' (full Jira JSON) for stub, synthetic raw for stub mode.
        """
        key = issue_key.strip().upper()
        if not key:
            raise JiraClientError("issue_key is required", status_code=400)

        if self._stub:
            data = _stub_issue(key)
            return {**data, "raw_payload": data["raw_payload"]}

        url = f"{self._base}/rest/api/3/issue/{key}"
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.get(
                    url,
                    headers={
                        "Authorization": self._auth_header(),
                        "Accept": "application/json",
                    },
                )
        except httpx.HTTPError as e:
            raise JiraClientError(f"Jira request failed: {e}") from e

        if r.status_code == 404:
            raise JiraClientError(f"Issue not found: {key}", status_code=404)
        if r.status_code == 401:
            raise JiraClientError(
                "Jira authentication failed (401). Check JIRA_EMAIL and JIRA_API_TOKEN.",
                status_code=401,
            )
        if r.status_code == 403:
            raise JiraClientError(
                "Jira access forbidden (403). The token may lack permission to read this issue.",
                status_code=403,
            )
        if r.status_code == 400:
            raise JiraClientError(
                f"Invalid Jira request for {key}: {r.text[:300]}".strip(),
                status_code=400,
            )
        if r.status_code >= 400:
            raise JiraClientError(
                f"Jira API error ({r.status_code}). {r.text[:400]}".strip(),
                status_code=r.status_code,
            )

        payload = r.json()
        normalized = _normalize_issue(key, payload)
        return {**normalized, "raw_payload": payload}

    def search_issues(self, jql: str, max_results: int = 20) -> dict[str, Any]:
        """Run JQL search; returns {issues: [{issue_key, summary, status}], total}."""
        jql = jql.strip()
        if not jql:
            raise JiraClientError("jql is required", status_code=400)
        max_results = max(1, min(max_results, 100))

        if self._stub:
            stub = _stub_search(jql, max_results)
            issues = []
            for item in stub.get("issues", [])[:max_results]:
                k = item.get("key", "STUB-1")
                fields = item.get("fields") or {}
                issues.append(
                    {
                        "issue_key": k,
                        "summary": str(fields.get("summary") or ""),
                        "status": (fields.get("status") or {}).get("name"),
                    }
                )
            return {"issues": issues, "total": stub.get("total")}

        url = f"{self._base}/rest/api/3/search"
        body = {"jql": jql, "maxResults": max_results, "fields": ["summary", "status"]}
        try:
            with httpx.Client(timeout=45.0) as client:
                r = client.post(
                    url,
                    headers={
                        "Authorization": self._auth_header(),
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.HTTPError as e:
            raise JiraClientError(f"Jira search failed: {e}") from e

        if r.status_code == 401:
            raise JiraClientError(
                "Jira authentication failed (401). Check JIRA_EMAIL and JIRA_API_TOKEN.",
                status_code=401,
            )
        if r.status_code == 403:
            raise JiraClientError(
                "Jira access forbidden (403). The token may lack permission to search issues.",
                status_code=403,
            )
        if r.status_code >= 400:
            raise JiraClientError(
                f"Jira search error ({r.status_code}): {r.text[:400]}".strip(),
                status_code=r.status_code,
            )

        data = r.json()
        issues = []
        for item in data.get("issues") or []:
            fields = item.get("fields") or {}
            issues.append(
                {
                    "issue_key": item.get("key", ""),
                    "summary": str(fields.get("summary") or ""),
                    "status": (fields.get("status") or {}).get("name"),
                }
            )
        return {"issues": issues, "total": data.get("total")}
