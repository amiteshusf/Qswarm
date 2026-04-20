"""GitHub REST API: create pull request (minimal, swappable for other hosts later)."""

from __future__ import annotations

from typing import Any

import httpx


class GitHubApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        self.message = message
        self.status_code = status_code
        self.body = body
        super().__init__(message)


def create_pull_request(
    *,
    token: str,
    owner: str,
    repo: str,
    title: str,
    body: str,
    head: str,
    base: str,
    api_base_url: str = "https://api.github.com",
    timeout_s: float = 60.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """
    POST /repos/{owner}/{repo}/pulls

    Returns a dict with at least ``number``, ``html_url``, ``title``, ``body``, ``head`` ref, ``base`` ref.
    """
    own = owner.strip()
    rep = repo.strip()
    if not own or not rep:
        raise GitHubApiError("owner and repo are required")
    url = f"{api_base_url.rstrip('/')}/repos/{own}/{rep}/pulls"
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "title": title[:500],
        "body": body[:65500] if body else "",
        "head": head.strip(),
        "base": base.strip(),
    }
    own_client = client is None
    c = client or httpx.Client(timeout=timeout_s)
    try:
        resp = c.post(url, headers=headers, json=payload)
    finally:
        if own_client:
            c.close()
    if resp.status_code not in (200, 201):
        raise GitHubApiError(
            f"GitHub API error: {resp.status_code}",
            status_code=resp.status_code,
            body=resp.text[:2000],
        )
    data = resp.json()
    return {
        "number": int(data["number"]),
        "html_url": str(data.get("html_url") or ""),
        "title": str(data.get("title") or title),
        "body": str(data.get("body") or body or ""),
        "head": str((data.get("head") or {}).get("ref") or head),
        "base": str((data.get("base") or {}).get("ref") or base),
    }
