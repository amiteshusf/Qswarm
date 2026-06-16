"""Contract tests for ``/api/v1/repo-connections`` vs Qswarm-UI ``repoConnectionSchema``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.db.session import get_db
from app.schemas.repository_connection import RepositoryConnectionResponse
from app.services.ui_v1_repo_connections import format_repo_connection_json_for_ui

# Qswarm-UI: https://github.com/amiteshusf/Qswarm-UI/blob/main/src/api/schemas.ts
_REQUIRED_REPO_CONN_KEYS = frozenset(
    {"id", "provider", "owner", "repo", "defaultBranch", "authRef", "createdAt", "updatedAt"}
)
_OPTIONAL_REPO_CONN_KEYS = frozenset({"displayName", "cloneUrl"})


@pytest.fixture
def ui_client(db_session):
    def _override():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _assert_row_matches_qswarm_ui_repo_connection(row: dict) -> None:
    keys = set(row.keys())
    assert _REQUIRED_REPO_CONN_KEYS <= keys, keys
    assert keys <= _REQUIRED_REPO_CONN_KEYS | _OPTIONAL_REPO_CONN_KEYS, keys
    for k in _REQUIRED_REPO_CONN_KEYS:
        assert isinstance(row[k], str), k
    if "displayName" in row:
        assert isinstance(row["displayName"], str)
    if "cloneUrl" in row:
        assert isinstance(row["cloneUrl"], str)
    assert row["provider"] in ("github", "gitlab", "bitbucket", "other")


def test_format_repo_connection_json_for_ui_qswarm_ui_shape() -> None:
    r = RepositoryConnectionResponse(
        id="00000000-0000-4000-8000-000000000001",
        provider="github",
        display_name="",
        owner_or_org="acme",
        repo_name="bff",
        project_or_workspace=None,
        clone_url=None,
        default_branch="main",
        auth_type="github_pat_env",
        credential_reference="secret",
        is_active=True,
        created_by="t",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
    )
    out = format_repo_connection_json_for_ui(r)
    _assert_row_matches_qswarm_ui_repo_connection(out)
    assert out["owner"] == "acme"
    assert out["repo"] == "bff"
    assert out["authRef"] == "secret"
    assert "displayName" not in out
    assert "cloneUrl" not in out


def test_format_repo_connection_provider_azure_maps_to_other() -> None:
    r = RepositoryConnectionResponse(
        id="00000000-0000-4000-8000-000000000002",
        provider="azure_devops",
        display_name="X",
        owner_or_org="a",
        repo_name="b",
        project_or_workspace=None,
        clone_url="https://example.com/x.git",
        default_branch="develop",
        auth_type="github_pat_env",
        credential_reference="ref",
        is_active=False,
        created_by="u",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
    )
    out = format_repo_connection_json_for_ui(r)
    assert out["provider"] == "other"
    assert out["displayName"] == "X"
    assert out["cloneUrl"] == "https://example.com/x.git"


def test_ui_v1_repo_connections_list_array_and_detail(ui_client):
    cr = ui_client.post(
        "/api/v1/repo-connections",
        json={
            "provider": "github",
            "owner": "org",
            "repo": "repo",
            "displayName": "Contract",
            "defaultBranch": "main",
            "authRef": "GITHUB_TOKEN",
        },
    )
    assert cr.status_code == 201, cr.text
    cid = cr.json()["id"]
    assert cr.json()["authRef"] == "GITHUB_TOKEN"
    assert cr.json()["displayName"] == "Contract"
    _assert_row_matches_qswarm_ui_repo_connection(cr.json())

    lst = ui_client.get("/api/v1/repo-connections")
    assert lst.status_code == 200
    body = lst.json()
    assert isinstance(body, list)
    for row in body:
        _assert_row_matches_qswarm_ui_repo_connection(row)

    one = ui_client.get(f"/api/v1/repo-connections/{cid}")
    assert one.status_code == 200
    _assert_row_matches_qswarm_ui_repo_connection(one.json())
    assert one.json()["authRef"] == "GITHUB_TOKEN"

    pu = ui_client.patch(f"/api/v1/repo-connections/{cid}", json={"authRef": "NEWREF"})
    assert pu.status_code == 200
    assert pu.json()["authRef"] == "NEWREF"
    _assert_row_matches_qswarm_ui_repo_connection(pu.json())


def test_ui_v1_repo_connections_post_accepts_legacy_aliases(ui_client):
    r = ui_client.post(
        "/api/v1/repo-connections",
        json={
            "provider": "github",
            "ownerOrOrg": "legacy-org",
            "repoName": "legacy-repo",
            "defaultBranch": "main",
            "authReference": "tok",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["owner"] == "legacy-org"
    assert r.json()["repo"] == "legacy-repo"
    assert r.json()["authRef"] == "tok"
