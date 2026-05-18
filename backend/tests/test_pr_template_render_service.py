"""Unit tests for session create-pr branch policy PR template rendering."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.pr_template_render_service import (
    ALLOWED_PR_TEMPLATE_KEYS,
    build_pr_template_context,
    validate_and_render_pr_template,
)
from app.source_control.errors import SourceControlConfigurationError


def _ctx():
    sid = uuid.uuid4()
    jid = uuid.uuid4()
    session = SimpleNamespace(
        id=sid,
        approved_case_id="CASE-1",
        coding_engine="stub",
        source_reference="jira/ABC-1",
    )
    job = SimpleNamespace(id=jid, approved_case_id="CASE-1")
    conn = SimpleNamespace(repo_name="webapp", owner_or_org="acme")
    return build_pr_template_context(
        session,
        job,
        repository_connection=conn,
        source_branch="feat/x",
        target_branch="main",
    )


def test_build_pr_template_context_minimum_fields():
    ctx = _ctx()
    assert ctx["session_id"]
    assert ctx["approved_case_id"] == "CASE-1"
    assert ctx["coding_engine"] == "stub"
    assert ctx["source_reference"] == "jira/ABC-1"
    assert ctx["source_branch"] == "feat/x"
    assert ctx["target_branch"] == "main"
    assert ctx["repo_name"] == "webapp"
    assert ctx["owner_or_org"] == "acme"
    assert set(ctx.keys()) == ALLOWED_PR_TEMPLATE_KEYS


def test_title_template_renders_approved_case_id():
    ctx = _ctx()
    out = validate_and_render_pr_template("PR {approved_case_id}", ctx)
    assert out == "PR CASE-1"
    assert "{" not in out


def test_body_template_renders_session_engine_source():
    ctx = _ctx()
    tpl = "Session: {session_id}\nEngine: {coding_engine}\nSource: {source_reference}"
    out = validate_and_render_pr_template(tpl, ctx)
    assert "Engine: stub" in out
    assert ctx["session_id"] in out
    assert "jira/ABC-1" in out
    assert "{session_id}" not in out


def test_unknown_placeholder_raises():
    ctx = _ctx()
    with pytest.raises(SourceControlConfigurationError) as ei:
        validate_and_render_pr_template("x {typo} y", ctx)
    assert ei.value.code == "pr_template_invalid_placeholder"
    assert "typo" in ei.value.message


def test_format_spec_rejected():
    ctx = _ctx()
    with pytest.raises(SourceControlConfigurationError) as ei:
        validate_and_render_pr_template("{session_id!r}", ctx)
    assert ei.value.code == "pr_template_unsupported_syntax"


def test_bracket_suffix_placeholder_rejected():
    ctx = _ctx()
    with pytest.raises(SourceControlConfigurationError) as ei:
        validate_and_render_pr_template("{session_id[0]}", ctx)
    assert ei.value.code == "pr_template_invalid_placeholder"


def test_literal_doubled_braces():
    ctx = _ctx()
    out = validate_and_render_pr_template("{{literal}} {session_id}", ctx)
    assert out.startswith("{literal}")
    assert ctx["session_id"] in out
