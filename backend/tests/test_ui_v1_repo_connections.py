"""``/api/v1/repo-connections`` — backend-first parity with legacy ``/repo-connections`` (camelCase JSON)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.db.session import get_db


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


def test_ui_v1_repo_connections_matches_backend_list_shape(ui_client):
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
    assert cr.json()["credentialReference"] == "GITHUB_TOKEN"
    assert cr.json()["displayName"] == "Contract"
    assert cr.json()["ownerOrOrg"] == "org"
    assert cr.json()["repoName"] == "repo"

    lst = ui_client.get("/api/v1/repo-connections")
    assert lst.status_code == 200
    body = lst.json()
    assert "items" in body and isinstance(body["items"], list)
    assert any(x["id"] == cid for x in body["items"])

    one = ui_client.get(f"/api/v1/repo-connections/{cid}")
    assert one.status_code == 200
    assert one.json()["credentialReference"] == "GITHUB_TOKEN"

    pu = ui_client.patch(f"/api/v1/repo-connections/{cid}", json={"authRef": "NEWREF"})
    assert pu.status_code == 200
    assert pu.json()["credentialReference"] == "NEWREF"


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
    assert r.json()["ownerOrOrg"] == "legacy-org"
    assert r.json()["repoName"] == "legacy-repo"
    assert r.json()["credentialReference"] == "tok"
