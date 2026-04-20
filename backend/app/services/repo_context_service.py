"""Deterministic repo context selection for Playwright jobs (bounded file lists)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SKIP_DIR_PARTS = frozenset(
    {"node_modules", ".git", "dist", "build", ".next", "coverage", ".turbo", "out"}
)

AUTH_BOOST_KEYWORDS = frozenset(
    {"login", "auth", "password", "reset", "otp", "forgot", "signin", "credential", "mailhog"}
)

SPEC_SUFFIXES = (".spec.ts", ".spec.js", ".spec.mjs", ".spec.cjs", ".test.ts", ".test.js")


class RepoContextError(Exception):
    """Raised when repo context cannot be collected safely."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _should_skip(path: Path) -> bool:
    return any(p in SKIP_DIR_PARTS for p in path.parts)


def _rel(repo: Path, p: Path) -> str:
    return str(p.relative_to(repo)).replace("\\", "/")


def _tokenize_case_spec(case_spec: dict[str, Any]) -> set[str]:
    parts: list[str] = []
    for k in ("title", "objective", "description"):
        v = case_spec.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.lower())
    for k in ("steps", "preconditions", "expected_results"):
        for x in case_spec.get(k) or []:
            if isinstance(x, str) and x.strip():
                parts.append(x.lower())
    blob = " ".join(parts)
    return {t for t in re.findall(r"[a-z0-9]+", blob) if len(t) > 1}


def _score_path(rel: str, case_tokens: set[str]) -> int:
    low = rel.lower()
    segs = re.findall(r"[a-z0-9]+", low)
    score = sum(1 for t in segs if t in case_tokens and len(t) > 1)
    for kw in AUTH_BOOST_KEYWORDS:
        if kw in low:
            score += 3
    return score


def _list_files_under(repo: Path, rel_dirs: list[str], *, suffixes: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for rd in rel_dirs:
        base = repo / rd
        if not base.is_dir():
            continue
        try:
            for p in base.rglob("*"):
                if _should_skip(p):
                    continue
                if p.is_file():
                    low = p.name.lower()
                    if any(low.endswith(sfx) for sfx in suffixes):
                        out.append(_rel(repo, p))
        except OSError as e:
            raise RepoContextError(f"Cannot read directory {rd}: {e}") from e
    return out


def _gather_candidate_paths(repo: Path, fw: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    def add(rel: str) -> None:
        if rel and rel not in seen:
            seen.add(rel)
            ordered.append(rel)

    for key in ("similar_test_files", "fixture_files", "config_files"):
        for rel in fw.get(key) or []:
            if isinstance(rel, str):
                add(rel)

    for d in fw.get("page_object_dirs") or []:
        if not isinstance(d, str):
            continue
        base = repo / d
        if not base.is_dir():
            continue
        try:
            for p in base.rglob("*"):
                if _should_skip(p) or not p.is_file():
                    continue
                if p.suffix.lower() in (".ts", ".tsx", ".js", ".jsx"):
                    add(_rel(repo, p))
        except OSError as e:
            raise RepoContextError(f"Cannot scan page objects under {d}: {e}") from e

    for d in fw.get("helper_dirs") or []:
        if not isinstance(d, str):
            continue
        base = repo / d
        if not base.is_dir():
            continue
        try:
            for p in base.rglob("*"):
                if _should_skip(p) or not p.is_file():
                    continue
                if p.suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                    add(_rel(repo, p))
        except OSError as e:
            raise RepoContextError(f"Cannot scan helpers under {d}: {e}") from e

    test_roots = []
    tr = fw.get("test_root")
    if isinstance(tr, str) and tr.strip():
        test_roots.append(tr.strip())
    for extra in ("e2e", "playwright", "__tests__", "tests"):
        if extra not in test_roots:
            test_roots.append(extra)

    extra_specs = _list_files_under(repo, test_roots, suffixes=SPEC_SUFFIXES)
    for rel in extra_specs:
        add(rel)

    return ordered


def collect_repo_context(
    repo_path: Path,
    framework_summary: dict[str, Any],
    case_spec: dict[str, Any],
) -> dict[str, Any]:
    """
    Score candidate files from framework summary + repo walk; return bounded context.

    Raises:
        RepoContextError: on filesystem errors while scanning.
    """
    if framework_summary.get("framework_type") != "playwright":
        raise RepoContextError("repo context collection is only implemented for Playwright")

    case_tokens = _tokenize_case_spec(case_spec)
    candidates = _gather_candidate_paths(repo_path, framework_summary)

    scored = [(rel, _score_path(rel, case_tokens)) for rel in candidates]
    scored.sort(key=lambda x: (-x[1], x[0]))

    def pick(filter_fn, cap: int) -> list[str]:
        out: list[str] = []
        for rel, _ in scored:
            if filter_fn(rel) and rel not in out:
                out.append(rel)
            if len(out) >= cap:
                break
        return out

    def is_spec(rel: str) -> bool:
        low = rel.lower()
        return any(low.endswith(sfx) for sfx in SPEC_SUFFIXES)

    def is_page(rel: str) -> bool:
        low = rel.lower()
        return "/pages/" in low or low.startswith("pages/") or "/page-objects/" in low or "/pom/" in low

    def is_fixture(rel: str) -> bool:
        low = rel.lower()
        return "fixture" in low or "/fixtures/" in low

    def is_helper(rel: str) -> bool:
        low = rel.lower()
        return (
            "/utils/" in low
            or low.startswith("utils/")
            or "/helpers/" in low
            or "/lib/" in low
            or low.startswith("lib/")
        )

    def is_config(rel: str) -> bool:
        low = rel.lower()
        return "playwright.config" in low or low.endswith("playwright.config.ts") or low.endswith(
            "playwright.config.js"
        )

    similar = pick(lambda r: is_spec(r), 8)
    pages = pick(lambda r: is_page(r) and not is_spec(r), 8)
    fixtures = pick(lambda r: is_fixture(r), 8)
    helpers = pick(lambda r: is_helper(r) and not is_spec(r), 8)
    configs = pick(lambda r: is_config(r), 5)

    notes: list[str] = []
    case_blob = " ".join(case_tokens)
    if any(kw in case_blob for kw in AUTH_BOOST_KEYWORDS) or any(
        any(k in rel.lower() for k in ("auth", "password", "otp", "forgot", "login")) for rel in similar[:5]
    ):
        notes.append("Case references auth/password/OTP; auth-related paths prioritized in scoring")

    if not similar and not pages:
        notes.append("No high-scoring test or page object files matched case tokens; lists may be sparse")

    return {
        "framework_type": "playwright",
        "selected_test_root": framework_summary.get("test_root"),
        "similar_test_files": similar,
        "related_page_objects": pages,
        "fixture_files": fixtures,
        "helper_files": helpers,
        "config_files": configs,
        "relevance_notes": notes,
    }
