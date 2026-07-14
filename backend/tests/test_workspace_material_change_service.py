"""Tests for engine-agnostic revision material-change validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.workspace_material_change_service import (
    RevisionNoMaterialChangeError,
    capture_workspace_snapshot,
    compare_workspace_snapshots,
    format_revision_no_material_change_message,
    require_material_workspace_change,
)


def test_capture_and_compare_detects_content_change(tmp_path: Path):
    target = tmp_path / "tests" / "smoke.spec.ts"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")

    before = capture_workspace_snapshot(tmp_path, ["tests/smoke.spec.ts"])
    target.write_text("after\n", encoding="utf-8")
    after = capture_workspace_snapshot(tmp_path, ["tests/smoke.spec.ts"])

    result = compare_workspace_snapshots(before, after)
    assert result.has_material_change is True
    assert result.changed_paths == ("tests/smoke.spec.ts",)
    assert result.unchanged_paths == ()
    assert result.before_hashes["tests/smoke.spec.ts"] != result.after_hashes["tests/smoke.spec.ts"]


def test_compare_reports_unchanged_when_identical(tmp_path: Path):
    target = tmp_path / "tests" / "smoke.spec.ts"
    target.parent.mkdir(parents=True)
    target.write_text("same\n", encoding="utf-8")

    snap = capture_workspace_snapshot(tmp_path, ["tests/smoke.spec.ts"])
    result = compare_workspace_snapshots(snap, snap)
    assert result.has_material_change is False
    assert result.changed_paths == ()
    assert result.unchanged_paths == ("tests/smoke.spec.ts",)
    assert result.failure_reason is not None


def test_require_material_workspace_change_raises_for_no_op(tmp_path: Path):
    target = tmp_path / "tests" / "inventory.spec.ts"
    target.parent.mkdir(parents=True)
    target.write_text("unchanged\n", encoding="utf-8")

    snap = capture_workspace_snapshot(tmp_path, ["tests/inventory.spec.ts"])
    with pytest.raises(RevisionNoMaterialChangeError) as exc:
        require_material_workspace_change(tmp_path, before=snap, after=snap)

    err = exc.value
    assert err.code == "revision_no_material_change"
    assert "tests/inventory.spec.ts" in err.message
    assert "no material workspace change" in err.message.lower()
    assert err.result.unchanged_paths == ("tests/inventory.spec.ts",)


def test_format_message_includes_scoped_paths():
    from app.services.workspace_material_change_service import MaterialChangeResult

    result = MaterialChangeResult(
        scoped_paths=("tests/a.ts", "tests/b.ts"),
        changed_paths=(),
        unchanged_paths=("tests/a.ts", "tests/b.ts"),
        before_hashes={"tests/a.ts": "x", "tests/b.ts": "y"},
        after_hashes={"tests/a.ts": "x", "tests/b.ts": "y"},
        has_material_change=False,
        failure_reason="noop",
    )
    msg = format_revision_no_material_change_message(result)
    assert "tests/a.ts" in msg
    assert "tests/b.ts" in msg
    assert "no material workspace change" in msg.lower()
