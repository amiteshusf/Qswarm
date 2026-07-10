"""Tests for temporary Copilot CLI command diagnostics."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.services.copilot_command_diagnostics import build_copilot_command_diagnostics


def test_build_diagnostics_reports_checks_for_existing_file(tmp_path: Path):
    script = tmp_path / "fake-copilot"
    script.write_text("#!/bin/sh\necho help\n", encoding="utf-8")
    script.chmod(0o755)

    settings = Settings(
        QSWARM_COPILOT_AGENT_ENABLED=True,
        QSWARM_COPILOT_AGENT_COMMAND=str(script),
    )
    out = build_copilot_command_diagnostics(settings, help_timeout_seconds=5.0)

    assert out["raw_command"] == str(script)
    assert out["checks"]["os_path_exists"] is True
    assert out["checks"]["os_path_isfile"] is True
    assert out["checks"]["os_access_x_ok"] is True
    assert out["checks"]["pathlib_is_file"] is True
    assert out["adapter_resolution"]["would_succeed"] is True
    assert out["help_probe"]["exit_code"] == 0


def test_build_diagnostics_missing_command():
    settings = Settings(
        QSWARM_COPILOT_AGENT_ENABLED=True,
        QSWARM_COPILOT_AGENT_COMMAND="definitely-not-a-real-binary-xyz",
    )
    out = build_copilot_command_diagnostics(settings, help_timeout_seconds=1.0)

    assert out["checks"]["os_path_exists"] is False
    assert out["checks"]["shutil_which"] is None
    assert out["adapter_resolution"]["would_succeed"] is False
    assert "failure_message" in out["adapter_resolution"]


def test_copilot_command_diagnostics_endpoint(client, monkeypatch, tmp_path: Path):
    script = tmp_path / "copilot-diag"
    script.write_text("#!/bin/sh\necho copilot-help\n", encoding="utf-8")
    script.chmod(0o755)

    monkeypatch.setenv("QSWARM_COPILOT_AGENT_COMMAND", str(script))
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_ENABLED", "true")

    r = client.get("/internal/diagnostics/copilot-command")
    assert r.status_code == 200
    body = r.json()
    assert body["raw_command"] == str(script)
    assert body["checks"]["os_path_isfile"] is True
    assert "path_env" in body
    assert body["adapter_resolution"]["would_succeed"] is True


def test_build_diagnostics_empty_command():
    settings = Settings(QSWARM_COPILOT_AGENT_ENABLED=False, QSWARM_COPILOT_AGENT_COMMAND="")
    out = build_copilot_command_diagnostics(settings)
    assert out["raw_command"] == ""
    assert any(e["step"] == "raw_command" for e in out["errors"])
