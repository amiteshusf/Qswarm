"""Deterministic stub provider — no external API; simulates model output for tests and local dev."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.providers.coding.base import CodeIntelligenceProvider
from app.services.framework_scan_service import FrameworkScanError, resolve_repo_path


def _case_blob(case: dict[str, Any]) -> str:
    parts = [
        str(case.get("title") or ""),
        str(case.get("objective") or ""),
        str(case.get("description") or ""),
        " ".join(x for x in (case.get("steps") or []) if isinstance(x, str)),
        " ".join(x for x in (case.get("expected_results") or []) if isinstance(x, str)),
    ]
    return " ".join(parts).lower()


def _pick_target(similar: list[str], blob: str) -> tuple[str, str]:
    """Return (target_path, action modify|create)."""
    if not similar:
        return ("tests/smoke.spec.ts", "modify")

    scored: list[tuple[int, str]] = []
    for rel in similar:
        low = rel.lower()
        score = 0
        if "forgot-password" in low or "forgot_password" in low:
            score += 20
        if "reset" in low and "password" in low:
            score += 15
        if "login" in low and ("auth" in blob or "password" in blob):
            score += 8
        if "otp" in blob and ("otp" in low or "verify" in low):
            score += 6
        if "password" in blob and "password" in low:
            score += 5
        scored.append((score, rel))

    scored.sort(key=lambda x: (-x[0], x[1]))
    best = scored[0][1]
    action = "modify"
    return (best, action)


def _page_objects_for_case(pages: list[str], blob: str) -> list[str]:
    out: list[str] = []
    for p in pages:
        low = p.lower()
        if not any(k in blob for k in ("password", "otp", "forgot", "reset", "login", "auth")):
            continue
        if any(x in low for x in ("forgot", "password", "login", "auth", "signin")):
            out.append(p)
    return out[:8]


def _ordered_plan_paths(plan: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("files_to_modify", "files_to_create"):
        for p in plan.get(key) or []:
            if isinstance(p, str):
                n = p.strip().replace("\\", "/")
                if n and n not in out:
                    out.append(n)
    return out


def _playwright_spec_stub(case: dict[str, Any]) -> str:
    title = str(case.get("title") or "Automation case").replace("'", "\\'")
    return f"""import {{ test, expect }} from '@playwright/test';

// QSwarm stub generation for: {title}
test.describe('{title}', () => {{
  test('covers case steps', async ({{ page }}) => {{
    await page.goto('/');
  }});
}});
"""


def _generic_ts_stub(path: str) -> str:
    base = Path(path).name.replace(".", "_")
    return f"// QSwarm stub module: {path}\nexport const qswarmStub_{base[:40]} = true;\n"


def _page_object_stub(previous: str | None) -> str:
    base = previous if previous else "export class PageStub {}\n"
    snippet = "\n// QSwarm stub: OTP helper hook\nexport function qswarmStubOtpLocator() { return true; }\n"
    if "qswarmStubOtpLocator" in base:
        return base
    return base.rstrip() + "\n" + snippet


def _reuse_helpers(helpers: list[str], fixtures: list[str], blob: str) -> list[str]:
    reuse: list[str] = []
    for h in helpers + fixtures:
        if not isinstance(h, str):
            continue
        low = h.lower()
        if "mailhog" in low and any(k in blob for k in ("otp", "mail", "password", "email")):
            if h not in reuse:
                reuse.append(h)
    for h in helpers + fixtures:
        if isinstance(h, str) and h not in reuse and len(reuse) < 10:
            reuse.append(h)
    return reuse[:10]


class StubCodingProvider(CodeIntelligenceProvider):
    """Heuristic plan from framework + case + repo context (bounded, explainable)."""

    @property
    def name(self) -> str:
        return "stub"

    def create_change_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        fw = payload.get("framework_summary") or {}
        case = payload.get("case_spec") or {}
        ctx = payload.get("repo_context") or {}

        ft = str(fw.get("framework_type") or "playwright")
        blob = _case_blob(case)
        sim = [x for x in (ctx.get("similar_test_files") or []) if isinstance(x, str)]
        pages = [x for x in (ctx.get("related_page_objects") or []) if isinstance(x, str)]
        helpers = [x for x in (ctx.get("helper_files") or []) if isinstance(x, str)]
        fixtures = [x for x in (ctx.get("fixture_files") or []) if isinstance(x, str)]
        configs = [x for x in (ctx.get("config_files") or []) if isinstance(x, str)]

        target, action = _pick_target(sim, blob)
        page_hits = _page_objects_for_case(pages, blob)
        reuse = _reuse_helpers(helpers, fixtures, blob)

        files_to_modify: list[str] = []
        files_to_create: list[str] = []
        if action == "modify":
            files_to_modify.append(target)
            for p in page_hits:
                if p not in files_to_modify:
                    files_to_modify.append(p)
            files_to_modify = files_to_modify[:12]
        else:
            files_to_create = [target][:1]
            files_to_modify = page_hits[:8]

        avoid = ["playwright.config.ts", "playwright.config.js", "playwright.config.mjs"]
        for c in configs:
            if isinstance(c, str) and c.lower() not in [x.lower() for x in avoid]:
                avoid.append(c)
        avoid = avoid[:8]

        rationale = [
            "Stub provider: target chosen from repo_context similar tests weighted by case keywords",
            "Page object touches limited to paths that match auth/password/OTP themes in the case",
            "Helpers and fixtures pulled from repo_context with mailhog prioritized when relevant",
        ]
        scope_notes = [
            "Prefer minimal edits to listed files; avoid broad refactors",
            "Reuse existing fixtures/helpers before adding new utilities",
        ]
        risk_notes = [
            "OTP/email flows may need environment-specific wiring (e.g. Mailhog) not visible in static scan",
        ]

        return {
            "framework_type": ft,
            "target_test_file": target,
            "action_on_target_test_file": action,
            "files_to_create": files_to_create,
            "files_to_modify": files_to_modify,
            "files_to_reuse": reuse,
            "files_to_avoid": avoid,
            "planning_rationale": rationale,
            "scope_notes": scope_notes,
            "risk_notes": risk_notes,
        }

    def generate_patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        plan = payload.get("change_plan") or {}
        case = payload.get("case_spec") or {}
        fw = payload.get("framework_summary") or {}
        ft = str(plan.get("framework_type") or fw.get("framework_type") or "playwright")
        target = str(plan.get("target_test_file") or "").strip().replace("\\", "/")

        root: Path | None = None
        rp = payload.get("repo_path")
        if isinstance(rp, str) and rp.strip():
            try:
                root = resolve_repo_path(rp)
            except FrameworkScanError:
                root = None

        paths = _ordered_plan_paths(plan)
        generated: list[dict[str, Any]] = []

        create_set = {
            x.strip().replace("\\", "/")
            for x in (plan.get("files_to_create") or [])
            if isinstance(x, str) and x.strip()
        }
        modify_set = {
            x.strip().replace("\\", "/")
            for x in (plan.get("files_to_modify") or [])
            if isinstance(x, str) and x.strip()
        }

        for rel in paths:
            if rel in create_set and rel not in modify_set:
                action = "create"
            elif rel in modify_set:
                action = "modify"
            elif rel in create_set:
                action = "create"
            else:
                action = "modify"

            if rel.endswith(".spec.ts") or rel.endswith(".spec.js") or "/tests/" in rel and rel.endswith(".ts"):
                if action == "modify" and root:
                    p = root / rel
                    if p.is_file():
                        content = _playwright_spec_stub(case)
                    else:
                        content = _playwright_spec_stub(case)
                else:
                    content = _playwright_spec_stub(case)
            elif "/pages/" in rel or rel.startswith("pages/"):
                prev: str | None = None
                if root:
                    try:
                        fp = root / rel
                        if fp.is_file():
                            prev = fp.read_text(encoding="utf-8")
                    except OSError:
                        prev = None
                content = _page_object_stub(prev)
            else:
                content = _generic_ts_stub(rel)

            generated.append({"path": rel, "action": action, "content": content})

        reuse = [x for x in (plan.get("files_to_reuse") or []) if isinstance(x, str)]
        notes = [
            "Stub: full-file rewrites for planned paths only",
            "Reused helpers/fixtures are listed in reused_files; no content emitted for them",
        ]
        if any("mailhog" in x.lower() for x in reuse):
            notes.append("MailHog helper left unchanged for OTP/email retrieval")

        return {
            "framework_type": ft,
            "target_test_file": target,
            "generated_files": generated,
            "reused_files": reuse[:25],
            "generation_notes": notes[:20],
        }

    def suggest_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deterministic small patch: only paths in change plan; reads disk when possible."""
        plan = payload.get("change_plan") or {}
        fa = payload.get("failure_analysis") or {}
        case = payload.get("case_spec") or {}
        fw = payload.get("framework_summary") or {}

        if fa.get("skipped"):
            return {"skipped": True, "reason": str(fa.get("reason") or "analysis marked skipped")}

        ft = str(plan.get("framework_type") or fw.get("framework_type") or "playwright")
        target = str(plan.get("target_test_file") or "").strip().replace("\\", "/")
        if not target:
            return {"skipped": True, "reason": "no target_test_file in plan"}

        modify_set = {
            x.strip().replace("\\", "/")
            for x in (plan.get("files_to_modify") or [])
            if isinstance(x, str) and x.strip()
        }
        create_set = {
            x.strip().replace("\\", "/")
            for x in (plan.get("files_to_create") or [])
            if isinstance(x, str) and x.strip()
        }
        allowed_paths = modify_set | create_set
        if target and target not in allowed_paths:
            return {"skipped": True, "reason": "target_test_file not in plan create/modify lists"}

        root: Path | None = None
        rp = payload.get("repo_path")
        if isinstance(rp, str) and rp.strip():
            try:
                root = resolve_repo_path(rp)
            except FrameworkScanError:
                root = None

        paths: list[str] = []
        if target in modify_set or target in create_set:
            paths.append(target)
        for p in _ordered_plan_paths(plan):
            if p != target and p in modify_set and len(paths) < 5:
                paths.append(p)

        generated: list[dict[str, Any]] = []
        ftype = str(fa.get("failure_type") or "")
        for rel in paths:
            action = "modify" if rel in modify_set else "create"
            if rel in create_set and rel not in modify_set:
                action = "create"
            elif rel in modify_set:
                action = "modify"
            prev = ""
            if root:
                try:
                    fp = root / rel
                    if fp.is_file():
                        prev = fp.read_text(encoding="utf-8")
                except OSError:
                    prev = ""
            if ftype in ("selector_issue", "timing_issue") and rel.endswith(".spec.ts"):
                snippet = "\n    // QSwarm stub repair: short settle before assertions\n    await page.waitForTimeout(300);\n"
                if snippet.strip() not in prev:
                    content = (prev or _playwright_spec_stub(case)) + snippet
                else:
                    content = prev or _playwright_spec_stub(case)
            elif ftype == "import_or_path_issue":
                content = (prev or "// file\n") + "\n// QSwarm stub repair: import path hint\n"
            elif "/pages/" in rel or rel.startswith("pages/"):
                content = _page_object_stub(prev or None)
            else:
                content = (prev or "// file\n") + "\n// QSwarm stub repair\n"

            generated.append({"path": rel, "action": action, "content": content})

        if not generated:
            return {"skipped": True, "reason": "stub could not build repair paths"}

        reuse = [x for x in (plan.get("files_to_reuse") or []) if isinstance(x, str)]
        notes = [
            f"Stub repair for failure_type={ftype or 'unknown'}",
            "Minimal file touches within change plan scope",
        ]
        return {
            "framework_type": ft,
            "target_test_file": target,
            "generated_files": generated,
            "reused_files": reuse[:25],
            "generation_notes": notes[:20],
        }

    def revise_after_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deterministic revision: plan-scoped subset; annotates target spec from instruction."""
        plan = payload.get("change_plan") or {}
        case = payload.get("case_spec") or {}
        fw = payload.get("framework_summary") or {}
        instruction = str(payload.get("reviewer_instruction") or "").strip()
        if not instruction:
            return {"skipped": True, "reason": "empty reviewer instruction"}

        ft = str(plan.get("framework_type") or fw.get("framework_type") or "playwright")
        target = str(plan.get("target_test_file") or "").strip().replace("\\", "/")
        if not target:
            return {"skipped": True, "reason": "no target_test_file in plan"}

        modify_set = {
            x.strip().replace("\\", "/")
            for x in (plan.get("files_to_modify") or [])
            if isinstance(x, str) and x.strip()
        }
        create_set = {
            x.strip().replace("\\", "/")
            for x in (plan.get("files_to_create") or [])
            if isinstance(x, str) and x.strip()
        }
        if target not in modify_set | create_set:
            return {"skipped": True, "reason": "target_test_file not in plan create/modify lists"}

        root: Path | None = None
        rp = payload.get("repo_path")
        if isinstance(rp, str) and rp.strip():
            try:
                root = resolve_repo_path(rp)
            except FrameworkScanError:
                root = None

        action = "modify" if target in modify_set else "create"
        prev = ""
        if root:
            try:
                fp = root / target
                if fp.is_file():
                    prev = fp.read_text(encoding="utf-8")
            except OSError:
                prev = ""
        safe_inst = instruction.replace("*/", "* /")
        marker = f"\n// QSwarm stub post-review revision: {safe_inst[:400]}\n"
        if target.endswith(".spec.ts") or target.endswith(".spec.js"):
            base = prev if prev.strip() else _playwright_spec_stub(case)
            content = base.rstrip() + marker if marker.strip() not in base else base
        else:
            content = (prev or "// file\n") + marker

        reuse = [x for x in (plan.get("files_to_reuse") or []) if isinstance(x, str)]
        notes = [
            "Stub revision from reviewer instruction (bounded)",
            "Only target_test_file touched in this stub path",
        ]
        return {
            "framework_type": ft,
            "target_test_file": target,
            "generated_files": [{"path": target, "action": action, "content": content}],
            "reused_files": reuse[:25],
            "generation_notes": notes[:20],
        }
