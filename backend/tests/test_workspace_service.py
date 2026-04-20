"""Workspace file application."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.workspace_service import WorkspaceApplyError, apply_generated_patch


def test_apply_creates_planned_file(tmp_path: Path):
    patch_files = [
        {"path": "tests/new.spec.ts", "action": "create", "content": "export const x = 1;\n"},
    ]
    r = apply_generated_patch(tmp_path, patch_files)
    assert r["success"] is True
    assert (tmp_path / "tests" / "new.spec.ts").read_text(encoding="utf-8") == "export const x = 1;\n"


def test_apply_modifies_existing_file(tmp_path: Path):
    p = tmp_path / "pages" / "P.ts"
    p.parent.mkdir(parents=True)
    p.write_text("old\n", encoding="utf-8")
    r = apply_generated_patch(
        tmp_path,
        [{"path": "pages/P.ts", "action": "modify", "content": "new\n"}],
    )
    assert r["success"] is True
    assert p.read_text(encoding="utf-8") == "new\n"


def test_apply_modify_missing_raises(tmp_path: Path):
    with pytest.raises(WorkspaceApplyError, match="missing"):
        apply_generated_patch(
            tmp_path,
            [{"path": "pages/Missing.ts", "action": "modify", "content": "x\n"}],
        )


def test_apply_create_when_exists_raises(tmp_path: Path):
    p = tmp_path / "tests" / "dup.spec.ts"
    p.parent.mkdir(parents=True)
    p.write_text("exists\n", encoding="utf-8")
    with pytest.raises(WorkspaceApplyError, match="already exists"):
        apply_generated_patch(
            tmp_path,
            [{"path": "tests/dup.spec.ts", "action": "create", "content": "x\n"}],
        )
