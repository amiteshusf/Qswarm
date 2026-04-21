"""Jira REST client (v3) with httpx: reads, search, and limited writes; stub mode for local development."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from app.core.config import Settings


class JiraClientError(Exception):
    """Raised when Jira returns an error or the request fails."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# Jira Cloud removed POST /rest/api/3/search (410 Gone); use enhanced JQL search.
_JIRA_SEARCH_JQL_PATH = "/rest/api/3/search/jql"


def _jira_error_messages_from_body(text: str) -> str:
    """Extract human-readable lines from Jira JSON error body (no secrets)."""
    if not (text or "").strip():
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text.strip()[:500]
    if not isinstance(data, dict):
        return text.strip()[:500]
    msgs = list(data.get("errorMessages") or [])
    if msgs:
        return "; ".join(str(m) for m in msgs)[:800]
    errs = data.get("errors")
    if isinstance(errs, dict) and errs:
        parts = [f"{k}: {v}" for k, v in list(errs.items())[:8]]
        return "; ".join(parts)[:800]
    return text.strip()[:500]


def _format_jira_search_http_error(*, path: str, status_code: int, body_text: str) -> str:
    detail = _jira_error_messages_from_body(body_text)
    base = f"Jira JQL search failed: POST {path} returned HTTP {status_code}."
    if detail:
        return f"{base} {detail}"
    return base


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
    status_category = status.get("statusCategory") or {}
    status_category_key = status_category.get("key")

    return {
        "issue_key": issue_key.upper(),
        "issue_id": str(payload.get("id") or "") or None,
        "summary": str(fields.get("summary") or ""),
        "description": description_text or None,
        "issue_type": issuetype.get("name"),
        "priority": priority.get("name"),
        "status": status.get("name"),
        "status_category_key": status_category_key,
        "assignee": assignee.get("displayName") or assignee.get("emailAddress"),
        "reporter": reporter.get("displayName") or reporter.get("emailAddress"),
        "labels": list(fields.get("labels") or []),
        "raw_payload": payload,
    }


def _stub_issue(issue_key: str) -> dict[str, Any]:
    key = issue_key.upper()
    labels = ["stub", "sprint1"]
    if key == "PICKUP-1":
        labels = ["qswarm-test-design", "stub"]
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
        "status_category_key": "indeterminate",
        "assignee": "Unassigned",
        "reporter": "stub.user@example.com",
        "labels": labels,
        "raw_payload": {"stub": True, "key": key},
    }


_STUB_CREATE_SEQ: list[int] = [0]


def project_key_from_issue_key(issue_key: str) -> str:
    """``NSP-678`` -> ``NSP`` (standard Jira key shape)."""
    key = issue_key.strip().upper()
    if "-" not in key:
        raise JiraClientError(f"Cannot derive Jira project key from issue key: {issue_key}", status_code=400)
    return key.rsplit("-", 1)[0]


def plain_lines_to_adf(lines: list[str]) -> dict[str, Any]:
    """Build minimal Atlassian Document Format from plain lines (one paragraph per line)."""
    content: list[dict[str, Any]] = []
    for line in lines:
        text = (line or "")[:12000]
        if not text.strip():
            continue
        content.append({"type": "paragraph", "content": [{"type": "text", "text": text}]})
    if not content:
        content = [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "(no description body)"}],
            }
        ]
    return {"type": "doc", "version": 1, "content": content}


def _stub_search(jql: str, max_results: int) -> dict[str, Any]:
    jql_l = jql.lower()
    if "qswarm-test-design" in jql_l:
        return {
            "issues": [
                {
                    "key": "PICKUP-1",
                    "fields": {
                        "summary": "Stub pickup story for local JQL polling",
                        "labels": ["qswarm-test-design"],
                        "issuetype": {"name": "Story"},
                        "status": {
                            "name": "To Do",
                            "statusCategory": {"key": "new", "name": "To Do"},
                        },
                    },
                }
            ],
            "total": 1,
        }
    return {
        "issues": [
            {
                "key": "STUB-1",
                "fields": {
                    "summary": f"Stub result for JQL: {jql[:40]}...",
                    "status": {"name": "Open", "statusCategory": {"key": "new"}},
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
                st = fields.get("status") or {}
                issues.append(
                    {
                        "issue_key": k,
                        "summary": str(fields.get("summary") or ""),
                        "status": st.get("name"),
                        "labels": list(fields.get("labels") or []),
                        "issue_type": (fields.get("issuetype") or {}).get("name"),
                        "status_category_key": (st.get("statusCategory") or {}).get("key"),
                    }
                )
            return {"issues": issues, "total": stub.get("total")}

        path = _JIRA_SEARCH_JQL_PATH
        url = f"{self._base}{path}"
        body = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "labels", "issuetype"],
        }
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
            raise JiraClientError(
                f"Jira JQL search request failed (POST {path}): {e}",
                status_code=None,
            ) from e

        body_text = r.text or ""

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
                _format_jira_search_http_error(
                    path=path, status_code=r.status_code, body_text=body_text
                ),
                status_code=r.status_code,
            )

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise JiraClientError(
                f"Jira JQL search returned invalid JSON from POST {path} (HTTP {r.status_code}).",
                status_code=r.status_code,
            ) from e

        issues = []
        for item in data.get("issues") or []:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields") or {}
            st = fields.get("status") or {}
            issue_key = str(item.get("key") or fields.get("key") or "").strip()
            issues.append(
                {
                    "issue_key": issue_key,
                    "summary": str(fields.get("summary") or ""),
                    "status": st.get("name"),
                    "labels": list(fields.get("labels") or []),
                    "issue_type": (fields.get("issuetype") or {}).get("name"),
                    "status_category_key": (st.get("statusCategory") or {}).get("key"),
                }
            )

        raw_total = data.get("total")
        if raw_total is None:
            raw_total = data.get("totalIssueCount")
        if raw_total is None:
            raw_total = len(issues)

        return {"issues": issues, "total": raw_total}

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float = 45.0,
    ) -> httpx.Response:
        if self._stub:
            raise JiraClientError("_request_json should not be called in stub mode", status_code=500)
        url = f"{self._base}{path}"
        try:
            with httpx.Client(timeout=timeout) as client:
                return client.request(
                    method,
                    url,
                    headers={
                        "Authorization": self._auth_header(),
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=json_body,
                )
        except httpx.HTTPError as e:
            raise JiraClientError(f"Jira {method} {path} failed: {e}", status_code=None) from e

    def _raise_write_error(self, path: str, r: httpx.Response, action: str) -> None:
        body = r.text or ""
        detail = _jira_error_messages_from_body(body)
        msg = f"Jira {action}: {path} returned HTTP {r.status_code}."
        if detail:
            msg = f"{msg} {detail}"
        raise JiraClientError(msg.strip(), status_code=r.status_code)

    def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        description_adf: dict[str, Any],
        issue_type_name: str = "Task",
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a Jira issue. Returns ``{"key": "PROJ-1", "id": "10001", ...}`` from API JSON.
        """
        pk = project_key.strip().upper()
        if not pk:
            raise JiraClientError("project_key is required", status_code=400)
        if not (summary or "").strip():
            raise JiraClientError("summary is required", status_code=400)

        if self._stub:
            _STUB_CREATE_SEQ[0] += 1
            n = _STUB_CREATE_SEQ[0]
            child_key = f"STUB-TC-{n:04d}"
            return {"key": child_key, "id": f"stub-gen-{n}", "self": f"stub://{child_key}"}

        path = "/rest/api/3/issue"
        fields: dict[str, Any] = {
            "project": {"key": pk},
            "summary": summary.strip()[:254],
            "issuetype": {"name": issue_type_name},
            "description": description_adf,
        }
        extra_labels = [str(x) for x in (labels or []) if str(x).strip()]
        if extra_labels:
            fields["labels"] = extra_labels[:20]

        r = self._request_json("POST", path, json_body={"fields": fields})
        if r.status_code not in (200, 201):
            self._raise_write_error(path, r, "create_issue")
        data = r.json()
        key = str(data.get("key") or "").strip()
        if not key:
            raise JiraClientError(
                f"Jira create_issue succeeded but response had no key: POST {path}",
                status_code=r.status_code,
            )
        return data

    def link_issues(
        self,
        *,
        inward_issue_key: str,
        outward_issue_key: str,
        link_type_name: str = "Relates",
    ) -> None:
        """Create an issue link between two issues (direction depends on link type semantics)."""
        ik = inward_issue_key.strip().upper()
        ok = outward_issue_key.strip().upper()
        if not ik or not ok:
            raise JiraClientError("link_issues requires both issue keys", status_code=400)
        if self._stub:
            return

        path = "/rest/api/3/issueLink"
        body = {
            "type": {"name": link_type_name},
            "inwardIssue": {"key": ik},
            "outwardIssue": {"key": ok},
        }
        r = self._request_json("POST", path, json_body=body)
        if r.status_code not in (200, 201):
            self._raise_write_error(path, r, "link_issues")

    def assign_issue(self, issue_key: str, account_id: str) -> None:
        """Assign issue to a user by Atlassian ``accountId``."""
        key = issue_key.strip().upper()
        aid = account_id.strip()
        if not key or not aid:
            raise JiraClientError("assign_issue requires issue_key and account_id", status_code=400)
        if self._stub:
            return

        path = f"/rest/api/3/issue/{key}/assignee"
        r = self._request_json("PUT", path, json_body={"accountId": aid})
        if r.status_code not in (200, 204):
            self._raise_write_error(path, r, "assign_issue")

    def add_comment(self, issue_key: str, body_adf: dict[str, Any]) -> None:
        """Add a comment on an issue (body must be ADF)."""
        key = issue_key.strip().upper()
        if not key:
            raise JiraClientError("issue_key is required", status_code=400)
        if self._stub:
            return

        path = f"/rest/api/3/issue/{key}/comment"
        r = self._request_json("POST", path, json_body={"body": body_adf})
        if r.status_code not in (200, 201):
            self._raise_write_error(path, r, "add_comment")

    def update_issue(
        self,
        issue_key: str,
        *,
        summary: str | None = None,
        description_adf: dict[str, Any] | None = None,
    ) -> None:
        """Update summary and/or description (ADF) on an existing issue."""
        key = issue_key.strip().upper()
        if not key:
            raise JiraClientError("issue_key is required", status_code=400)
        fields: dict[str, Any] = {}
        if summary is not None and summary.strip():
            fields["summary"] = summary.strip()[:254]
        if description_adf is not None:
            fields["description"] = description_adf
        if not fields:
            return
        if self._stub:
            return

        path = f"/rest/api/3/issue/{key}"
        r = self._request_json("PUT", path, json_body={"fields": fields})
        if r.status_code not in (200, 204):
            self._raise_write_error(path, r, "update_issue")
