"""Playwright test repo detection and filesystem scan."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.adapters.framework.base import FrameworkAdapter

PLAYWRIGHT_CONFIG_NAMES = (
    "playwright.config.ts",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.cjs",
)

SKIP_DIR_PARTS = frozenset(
    {"node_modules", ".git", "dist", "build", ".next", "coverage", ".turbo", "out"}
)

TEST_SUFFIXES = (".spec.ts", ".spec.js", ".spec.mjs", ".spec.cjs", ".test.ts", ".test.js")


def _should_skip(path: Path) -> bool:
    return any(p in SKIP_DIR_PARTS for p in path.parts)


def _rel(repo: Path, p: Path) -> str:
    try:
        return str(p.relative_to(repo)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


class PlaywrightAdapter(FrameworkAdapter):
    @property
    def name(self) -> str:
        return "playwright"

    def detect(self, repo_path: Path) -> bool:
        if not repo_path.is_dir():
            return False
        for name in PLAYWRIGHT_CONFIG_NAMES:
            if (repo_path / name).is_file():
                return True
        pkg = repo_path / "package.json"
        if pkg.is_file():
            try:
                data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                return False
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                block = data.get(key)
                if isinstance(block, dict) and "@playwright/test" in block:
                    return True
        return False

    def scan(self, repo_path: Path) -> dict[str, Any]:
        config_files: list[str] = []
        for name in PLAYWRIGHT_CONFIG_NAMES:
            p = repo_path / name
            if p.is_file():
                config_files.append(name)

        language = "typescript"
        if config_files:
            cf = config_files[0].lower()
            if cf.endswith(".js") or cf.endswith(".cjs") or cf.endswith(".mjs"):
                language = "javascript"

        package_manager = self._infer_package_manager(repo_path)
        spec_files: list[str] = []
        for p in repo_path.rglob("*"):
            if _should_skip(p):
                continue
            if not p.is_file():
                continue
            low = p.name.lower()
            if any(low.endswith(sfx) for sfx in TEST_SUFFIXES):
                spec_files.append(_rel(repo_path, p))

        spec_files.sort()
        similar_test_files = spec_files[:15]

        test_root = self._infer_test_root(repo_path, spec_files)

        page_object_dirs = self._find_special_dirs(
            repo_path, {"pages", "page-objects", "pom", "poms", "page_objects"}
        )
        helper_dirs = self._find_special_dirs(
            repo_path, {"utils", "helpers", "lib", "support"}, under_tests=True
        )
        fixture_files = self._find_fixtures(repo_path)

        notes: list[str] = []
        if not config_files:
            notes.append("Detected via package.json only; no playwright.config.* at repo root")
        missing: list[str] = []
        if not spec_files:
            missing.append("No Playwright-style spec files found (*.spec.* / *.test.*)")

        return {
            "framework_type": "playwright",
            "language": language,
            "package_manager": package_manager,
            "config_files": config_files,
            "test_root": test_root,
            "runner_command": "npx playwright test",
            "test_file_patterns": ["*.spec.ts", "*.spec.js", "*.test.ts", "*.test.js"],
            "page_object_dirs": page_object_dirs,
            "fixture_files": fixture_files[:20],
            "helper_dirs": helper_dirs,
            "similar_test_files": similar_test_files,
            "notes": notes,
            "missing_capabilities": missing,
        }

    def _infer_package_manager(self, repo: Path) -> str | None:
        if (repo / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (repo / "yarn.lock").is_file():
            return "yarn"
        if (repo / "package-lock.json").is_file() or (repo / "npm-shrinkwrap.json").is_file():
            return "npm"
        if (repo / "package.json").is_file():
            return "npm"
        return None

    def _infer_test_root(self, repo: Path, spec_rels: list[str]) -> str | None:
        candidates = ("tests", "e2e", "playwright", "__tests__", "test")
        counts: dict[str, int] = {c: 0 for c in candidates}
        for rel in spec_rels:
            parts = rel.split("/")
            if parts and parts[0] in counts:
                counts[parts[0]] += 1
        best = max(counts, key=lambda k: counts[k])
        if counts[best] > 0:
            return best
        if spec_rels:
            parts = spec_rels[0].split("/")
            return parts[0] if len(parts) > 1 else "."
        for c in candidates:
            if (repo / c).is_dir():
                return c
        return None

    def _find_special_dirs(
        self, repo: Path, names: set[str], *, under_tests: bool = False
    ) -> list[str]:
        found: set[str] = set()
        roots = [repo]
        if under_tests:
            for sub in ("tests", "e2e", "playwright", "__tests__"):
                d = repo / sub
                if d.is_dir():
                    roots.append(d)
        for root in roots:
            try:
                for child in root.iterdir():
                    if child.is_dir() and child.name in names and not _should_skip(child):
                        found.add(_rel(repo, child))
            except OSError:
                continue
        return sorted(found)

    def _find_fixtures(self, repo: Path) -> list[str]:
        out: list[str] = []
        pattern = re.compile(r"fixture", re.I)
        for root_name in ("tests", "e2e", "playwright", "__tests__"):
            base = repo / root_name
            if not base.is_dir():
                continue
            for p in base.rglob("*"):
                if _should_skip(p):
                    continue
                if p.is_file() and pattern.search(p.name) and p.suffix in (
                    ".ts",
                    ".js",
                    ".mjs",
                    ".cjs",
                ):
                    out.append(_rel(repo, p))
        return sorted(out)[:20]
