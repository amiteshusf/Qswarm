"""
Deterministic case enhancement: raw optional inputs -> normalized case_spec_json.

No LLM. Intended to be replaced or augmented later.
"""

from __future__ import annotations

import re
from typing import Any


def _clean_str(s: str | None) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    return t if t else None


def _clean_str_list(items: list[str] | None) -> list[str]:
    if not items:
        return []
    out: list[str] = []
    for x in items:
        if not isinstance(x, str):
            continue
        t = x.strip()
        if t:
            out.append(t)
    return out


def _infer_objective(title: str, description: str | None) -> str:
    if description:
        first = re.split(r"[.\n]", description.strip(), maxsplit=1)[0].strip()
        if len(first) > 12:
            return first[:500]
    return f"Automate and verify: {title}"


def _automation_notes_from_text(blob: str) -> list[str]:
    low = blob.lower()
    notes: list[str] = []
    if any(k in low for k in ("otp", "one-time", "verification code")):
        notes.append("Flow may require OTP or out-of-band verification; stub or mail capture needed for automation")
    if any(k in low for k in ("password", "reset password", "forgot password")):
        notes.append("Password reset flows often need email/Mailhog or similar for assertions")
    if any(k in low for k in ("login", "sign in", "signin", "authenticate")):
        notes.append("Authentication state and session handling should be explicit in tests")
    return notes


def run_case_enhancement(
    approved_case_id: str,
    *,
    case_title: str | None = None,
    case_description: str | None = None,
    preconditions: list[str] | None = None,
    steps: list[str] | None = None,
    expected_results: list[str] | None = None,
) -> dict[str, Any]:
    """Build normalized case spec from external id and optional structured hints."""
    case_id = str(approved_case_id).strip()
    title = _clean_str(case_title) or f"Automation case {case_id}"
    description = _clean_str(case_description)
    pre = _clean_str_list(preconditions)
    st = _clean_str_list(steps)
    exp = _clean_str_list(expected_results)

    missing: list[str] = []
    if not st:
        missing.append("steps not provided")
    if not exp:
        missing.append("expected_results not provided")

    objective = _infer_objective(title, description)
    blob = " ".join([title, objective, description or "", " ".join(st), " ".join(exp), " ".join(pre)])
    auto_notes = _automation_notes_from_text(blob)

    ambiguities: list[str] = []
    if not description and not st:
        ambiguities.append("No narrative description or steps; scope inferred from title only")

    return {
        "approved_case_id": case_id,
        "title": title,
        "objective": objective,
        "description": description,
        "preconditions": pre,
        "steps": st,
        "expected_results": exp,
        "automation_notes": auto_notes,
        "ambiguities": ambiguities,
        "missing_information": missing,
    }
