"""Deterministic execution failure classification (bounded output)."""

from __future__ import annotations

import re
from typing import Any

MAX_EVIDENCE = 8
MAX_EVIDENCE_LEN = 300
MAX_ROOT_CAUSE = 500
MAX_CLARIFICATION = 400

_FAILURE_TYPES = frozenset(
    {
        "selector_issue",
        "timing_issue",
        "import_or_path_issue",
        "missing_helper_or_fixture",
        "missing_environment_capability",
        "missing_test_data",
        "tooling_or_launch_issue",
        "likely_product_failure",
        "unknown_failure",
    }
)


def _clip(s: str, n: int) -> str:
    t = str(s).strip()
    return t[:n] if len(t) > n else t


def analyze_execution_failure(execution_result: dict[str, Any]) -> dict[str, Any]:
    """
    Inspect bounded ``execution_result_json`` and return ``failure_analysis_json`` shape.

    Heuristics are ordered: launch → mail/OTP env → tooling → imports →
    high-signal locator timeouts → assertions (product) → broad locator → timing → unknown.
    """
    ex = execution_result if isinstance(execution_result, dict) else {}
    stdout = str(ex.get("stdout_tail") or "")
    stderr = str(ex.get("stderr_tail") or "")
    notes = ex.get("notes") or []
    notes_blob = " ".join(str(n) for n in notes if isinstance(n, str))
    blob = f"{stdout} {stderr} {notes_blob}".lower()
    launch = str(ex.get("launch_error") or "").lower()
    exit_code = ex.get("exit_code")

    evidence: list[str] = []

    def add_ev(msg: str) -> None:
        if len(evidence) < MAX_EVIDENCE:
            evidence.append(_clip(msg, MAX_EVIDENCE_LEN))

    # Tooling / launch (only when not clearly mail/OTP infra — see below)
    if launch:
        add_ev("launch_error field set on execution result")
        return _finalize(
            "tooling_or_launch_issue",
            repairable=False,
            needs_human_input=False,
            root="Playwright or Node tooling failed to run; not safely auto-repaired",
            evidence=evidence,
            action="no_action",
            question=None,
        )

    # Missing environment / OTP infra (before generic ECONNREFUSED → tooling)
    if "mailhog" in blob or "mail hog" in blob:
        if any(x in blob for x in ("econnrefused", "connect", "refused", "unavailable", "missing")):
            add_ev("Mail or OTP-related connectivity mentioned in output")
            return _finalize(
                "missing_environment_capability",
                repairable=False,
                needs_human_input=True,
                root="OTP or mail capture (e.g. MailHog) appears unavailable in this environment",
                evidence=evidence,
                action="ask_human",
                question="How should OTP or outbound email be retrieved in this environment?",
            )

    if re.search(r"\b(enoent|econnrefused|spawn err)\b", blob) or (
        "npx" in blob and "not found" in blob
    ):
        add_ev("ENOENT / connection / npx resolution pattern in output")
        return _finalize(
            "tooling_or_launch_issue",
            repairable=False,
            needs_human_input=False,
            root="Playwright or Node tooling failed to run; not safely auto-repaired",
            evidence=evidence,
            action="no_action",
            question=None,
        )

    # Missing test data / 404-style
    if re.search(r"\b404\b|test data|fixture data|seed data", blob):
        add_ev("Output references missing data or HTTP 404")
        return _finalize(
            "missing_test_data",
            repairable=False,
            needs_human_input=True,
            root="Test may depend on data or routes that are not present",
            evidence=evidence,
            action="ask_human",
            question="What test data or environment setup is required for this flow?",
        )

    # Import / module resolution
    if any(
        p in blob
        for p in (
            "cannot find module",
            "cannot resolve",
            "err_module_not_found",
            "error: cannot find",
            "module not found",
        )
    ):
        add_ev("Module resolution or import error mentioned")
        return _finalize(
            "import_or_path_issue",
            repairable=True,
            needs_human_input=False,
            root="Import or path resolution failed; a targeted patch may fix paths or imports",
            evidence=evidence,
            action="repair_patch",
            question=None,
        )

    if "fixture" in blob and ("not found" in blob or "undefined" in blob):
        add_ev("Fixture or helper reference issue")
        return _finalize(
            "missing_helper_or_fixture",
            repairable=True,
            needs_human_input=False,
            root="Fixture or helper wiring may be incorrect or missing",
            evidence=evidence,
            action="repair_patch",
            question=None,
        )

    # High-signal locator / strict / visibility (before broad "locator" and expect() lines)
    if any(
        p in blob
        for p in (
            "waiting for locator",
            "strict mode violation",
            "element is not attached",
            "element is not visible",
        )
    ) or re.search(r"timeout \d+ms exceeded", blob):
        add_ev("Locator or visibility timeout pattern in stderr/stdout")
        return _finalize(
            "selector_issue",
            repairable=True,
            needs_human_input=False,
            root="Locator timeout or strict mode suggests brittle or incorrect selectors",
            evidence=evidence,
            action="repair_patch",
            question=None,
        )

    # Assertion / expectation (likely product) — before generic substring "locator"
    if any(p in blob for p in ("expect(", "expect failed", "assertionerror", "tohave", "tobe")):
        add_ev("Assertion or expectation failure in output")
        return _finalize(
            "likely_product_failure",
            repairable=False,
            needs_human_input=False,
            root="Assertions failed after actions; likely application behavior or wrong expectations",
            evidence=evidence,
            action="no_action",
            question=None,
        )

    if "locator" in blob:
        add_ev("Output mentions locator resolution")
        return _finalize(
            "selector_issue",
            repairable=True,
            needs_human_input=False,
            root="Locator-related message suggests brittle or incorrect selectors",
            evidence=evidence,
            action="repair_patch",
            question=None,
        )

    # Generic timeout (timing)
    if "timed out" in blob or exit_code == 124:
        add_ev("Execution timed out")
        return _finalize(
            "timing_issue",
            repairable=True,
            needs_human_input=False,
            root="Global timeout may indicate slow app or missing readiness waits",
            evidence=evidence,
            action="repair_patch",
            question=None,
        )

    add_ev(f"exit_code={exit_code!r}; no specific classifier matched")
    return _finalize(
        "unknown_failure",
        repairable=False,
        needs_human_input=False,
        root="Failure pattern not recognized; manual investigation recommended",
        evidence=evidence,
        action="no_action",
        question=None,
    )


def _finalize(
    failure_type: str,
    *,
    repairable: bool,
    needs_human_input: bool,
    root: str,
    evidence: list[str],
    action: str,
    question: str | None,
) -> dict[str, Any]:
    if failure_type not in _FAILURE_TYPES:
        failure_type = "unknown_failure"
    out: dict[str, Any] = {
        "failure_type": failure_type,
        "repairable": repairable,
        "needs_human_input": needs_human_input,
        "root_cause_summary": _clip(root, MAX_ROOT_CAUSE),
        "evidence": [_clip(e, MAX_EVIDENCE_LEN) for e in evidence[:MAX_EVIDENCE]],
        "suggested_action": action,
    }
    if question and needs_human_input:
        out["clarification_question"] = _clip(question, MAX_CLARIFICATION)
    return out
