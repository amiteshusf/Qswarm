"""Copilot CLI argv construction and Settings wiring (config-driven, no real Copilot binary)."""

from __future__ import annotations

import sys

import pytest

from app.automation_engine.copilot_agent_adapter import (
    build_copilot_cli_argv,
    parse_copilot_extra_args,
    summarize_copilot_argv,
)
from app.core.config import Settings, get_settings


def _settings(
    *,
    command: str,
    extra_args: str = "",
    timeout_seconds: int = 600,
) -> Settings:
    return Settings(
        QSWARM_COPILOT_AGENT_COMMAND=command,
        QSWARM_COPILOT_AGENT_EXTRA_ARGS=extra_args,
        QSWARM_COPILOT_AGENT_TIMEOUT_SECONDS=timeout_seconds,
    )


def _fake_cli_exe(tmp_path) -> str:
    exe = tmp_path / "copilot"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    return str(exe.resolve())


def test_parse_copilot_extra_args_empty():
    assert parse_copilot_extra_args("") == []
    assert parse_copilot_extra_args("   ") == []


def test_parse_copilot_extra_args_shlex_split():
    assert parse_copilot_extra_args("--allow-tool=write --allow-all-paths") == [
        "--allow-tool=write",
        "--allow-all-paths",
    ]


def test_build_argv_no_extra_args(tmp_path):
    exe = _fake_cli_exe(tmp_path)
    s = _settings(command=exe)
    argv, inv = build_copilot_cli_argv(s, "do the thing")
    assert argv == [exe, "-p", "do the thing"]
    assert inv["executable"] == exe
    assert inv["extra_args"] == []
    assert inv["extra_args_raw"] == ""
    assert inv["prompt_flag"] == "-p"
    assert inv["argv_prefix"] == [exe, "-p"]
    assert inv["prompt_char_count"] == len("do the thing")
    assert inv["timeout_seconds"] == 600


def test_build_argv_with_allow_flags(tmp_path):
    exe = _fake_cli_exe(tmp_path)
    s = _settings(
        command=exe,
        extra_args="--allow-all-tools --allow-all-paths",
        timeout_seconds=900,
    )
    argv, inv = build_copilot_cli_argv(s, "revise tests")
    assert argv == [
        exe,
        "--allow-all-tools",
        "--allow-all-paths",
        "-p",
        "revise tests",
    ]
    assert inv["extra_args"] == ["--allow-all-tools", "--allow-all-paths"]
    assert inv["extra_args_raw"] == "--allow-all-tools --allow-all-paths"
    assert inv["timeout_seconds"] == 900


def test_build_argv_when_extras_include_print_flag(tmp_path):
    exe = _fake_cli_exe(tmp_path)
    s = _settings(command=exe, extra_args="--print")
    argv, inv = build_copilot_cli_argv(s, "already flagged")
    assert argv == [exe, "--print", "already flagged"]
    assert inv["prompt_flag"] == "--print"
    assert "-p" not in argv[:-1]


def test_build_argv_when_extras_include_p_flag(tmp_path):
    exe = _fake_cli_exe(tmp_path)
    s = _settings(command=exe, extra_args="-p")
    argv, inv = build_copilot_cli_argv(s, "prompt tail")
    assert argv == [exe, "-p", "prompt tail"]
    assert inv["prompt_flag"] == "-p"


def test_summarize_copilot_argv_omits_long_prompt(tmp_path):
    exe = _fake_cli_exe(tmp_path)
    argv = [exe, "--allow-all-paths", "-p", "x" * 500]
    summary = summarize_copilot_argv(argv)
    assert summary["argv_prefix"] == [exe, "--allow-all-paths", "-p"]
    assert summary["prompt_char_count"] == 500


def test_settings_reads_copilot_extra_args_from_env(monkeypatch):
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_COMMAND", sys.executable)
    monkeypatch.setenv(
        "QSWARM_COPILOT_AGENT_EXTRA_ARGS",
        "--allow-all-tools --allow-all-paths",
    )
    monkeypatch.setenv("QSWARM_COPILOT_AGENT_TIMEOUT_SECONDS", "750")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.qswarm_copilot_agent_command == sys.executable
        assert s.qswarm_copilot_agent_extra_args == "--allow-all-tools --allow-all-paths"
        assert s.qswarm_copilot_agent_timeout_seconds == 750
        argv, inv = build_copilot_cli_argv(s, "task")
        assert "--allow-all-tools" in argv
        assert "--allow-all-paths" in argv
        assert inv["extra_args"] == ["--allow-all-tools", "--allow-all-paths"]
    finally:
        monkeypatch.delenv("QSWARM_COPILOT_AGENT_COMMAND", raising=False)
        monkeypatch.delenv("QSWARM_COPILOT_AGENT_EXTRA_ARGS", raising=False)
        monkeypatch.delenv("QSWARM_COPILOT_AGENT_TIMEOUT_SECONDS", raising=False)
        get_settings.cache_clear()
