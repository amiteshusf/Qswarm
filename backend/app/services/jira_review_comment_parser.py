"""Deterministic @QSwarm comment parsing (no LLM)."""

from __future__ import annotations

import re
from typing import Any, Literal


def _infer_target_scope(text_lower: str) -> str:
    m = re.search(r"case\s*:\s*(\d+)", text_lower)
    if m:
        return f"case:{int(m.group(1))}"
    if "negative" in text_lower:
        return "negative_cases"
    if "edge" in text_lower:
        return "edge_cases"
    return "all"


def parse_qswarm_review_comment(raw: str) -> dict[str, Any] | None:
    """
    If the comment mentions ``@QSwarm`` (case-insensitive), return instruction metadata.

    Returns ``None`` when there is no QSwarm mention (caller should ignore without persisting).
    """
    if not (raw or "").strip():
        return None
    lower = raw.lower()
    if "@qswarm" not in lower:
        return None

    idx = lower.find("@qswarm")
    tail = (raw[idx + len("@qswarm") :]).strip()
    if tail.startswith(","):
        tail = tail[1:].strip()

    tl = tail.lower()
    if "regenerate" in tl:
        action: Literal["refine", "regenerate", "unknown"] = "regenerate"
    elif tail.strip():
        action = "refine"
    else:
        action = "unknown"

    return {
        "instruction_text": tail.strip() or raw.strip(),
        "parsed_action_type": action,
        "target_scope": _infer_target_scope(tl),
    }
