"""Short delta-only lines for Jira reply comments (no full design repost)."""

from __future__ import annotations

from typing import Any


def _scenario_titles(content: dict[str, Any]) -> list[str]:
    ss = content.get("scenario_set") or []
    if not isinstance(ss, list):
        return []
    out: list[str] = []
    for s in ss:
        if isinstance(s, dict) and str(s.get("title") or "").strip():
            out.append(str(s["title"]).strip()[:200])
    return out


def build_delta_comment_lines(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    action: str,
    feedback_text: str,
    new_version_number: int,
) -> list[str]:
    """Deterministic delta summary for a Jira thread reply."""
    fb = (feedback_text or "").lower()
    before_t = _scenario_titles(before)
    after_t = _scenario_titles(after)
    lines: list[str] = []

    if action == "regenerate":
        lines.append("Regenerated the internal draft per your comment.")
        if "minimal" in fb or "positive only" in fb:
            lines.append("- Applied minimal / positive-focused shaping where applicable.")
        if "positive and negative" in fb or "positive and negative only" in fb:
            lines.append("- Shaped toward a small positive + negative set.")
        if after_t:
            lines.append("Current scenario titles:")
            for i, t in enumerate(after_t[:5], start=1):
                lines.append(f"  {i}. {t}")
    else:
        if "negative" in fb:
            lines.append("Added or reinforced negative coverage where applicable:")
            new_neg = [t for t in after_t if t not in before_t]
            if new_neg:
                for i, t in enumerate(new_neg[:4], start=1):
                    lines.append(f"  {i}. {t}")
            else:
                lines.append("  (negative path already present; wording may have been adjusted.)")
        if "stepwise" in fb or "detailed" in fb:
            lines.append("Updated step detail for the current draft (expanded outlines / expected results).")
        if "minimal" in fb:
            lines.append("Reduced scenario breadth toward a minimal slice.")
        if not lines:
            lines.append("Applied targeted refinements to the current draft per your feedback.")

    lines.append(f"Internal draft updated to version {new_version_number}.")
    return lines
