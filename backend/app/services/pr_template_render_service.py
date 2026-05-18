"""Strict placeholder rendering for branch-policy PR title/body templates (provider-agnostic)."""

from __future__ import annotations

from string import Formatter
from typing import Mapping

from app.db.models.automation_job import AutomationJob
from app.db.models.automation_session import AutomationSession
from app.db.models.repository_connection import RepositoryConnection
from app.source_control.errors import SourceControlConfigurationError

# Placeholders allowed in ``pr_title_template`` / ``pr_body_template`` (create-pr).
ALLOWED_PR_TEMPLATE_KEYS: frozenset[str] = frozenset(
    {
        "session_id",
        "approved_case_id",
        "coding_engine",
        "source_reference",
        "job_id",
        "repo_name",
        "owner_or_org",
        "target_branch",
        "source_branch",
    }
)


def build_pr_template_context(
    session: AutomationSession,
    job: AutomationJob,
    *,
    repository_connection: RepositoryConnection,
    source_branch: str,
    target_branch: str,
) -> dict[str, str]:
    """Flat string context for :func:`validate_and_render_pr_template`."""
    return {
        "session_id": str(session.id),
        "approved_case_id": (session.approved_case_id or job.approved_case_id or "")[:512],
        "coding_engine": (session.coding_engine or "").strip()[:64],
        "source_reference": (session.source_reference or "").strip()[:512],
        "job_id": str(job.id),
        "repo_name": (repository_connection.repo_name or "").strip()[:256],
        "owner_or_org": (repository_connection.owner_or_org or "").strip()[:256],
        "target_branch": (target_branch or "").strip()[:256],
        "source_branch": (source_branch or "").strip()[:512],
    }


def validate_and_render_pr_template(template: str, context: Mapping[str, str]) -> str:
    """
    Render ``template`` using only keys in ``ALLOWED_PR_TEMPLATE_KEYS``.

    - Rejects unknown placeholders with :class:`SourceControlConfigurationError`.
    - Rejects format_spec / conversion (e.g. ``{x:5d}``) for predictable behavior.
    - Placeholder names must match allowed keys exactly (no attributes or indices).
    - Literal doubled braces ``{{`` / ``}}`` are supported via :class:`string.Formatter`.
    """
    if not template:
        return ""
    formatter = Formatter()
    for _lit, field_name, format_spec, conversion in formatter.parse(template):
        if field_name is None:
            continue
        if format_spec or conversion:
            raise SourceControlConfigurationError(
                "PR template placeholders must be simple names only (no format_spec or conversion)",
                code="pr_template_unsupported_syntax",
            )
        raw = field_name.strip()
        if not raw:
            raise SourceControlConfigurationError(
                "Empty PR template placeholder",
                code="pr_template_invalid_placeholder",
            )
        if raw not in ALLOWED_PR_TEMPLATE_KEYS:
            raise SourceControlConfigurationError(
                f"Unknown PR template placeholder: {{{field_name}}}",
                code="pr_template_invalid_placeholder",
            )

    kwargs = {k: str(context.get(k, "")) for k in ALLOWED_PR_TEMPLATE_KEYS}
    try:
        return formatter.vformat(template, (), kwargs)
    except KeyError as e:
        raise SourceControlConfigurationError(
            f"PR template referenced unknown key: {e!s}",
            code="pr_template_invalid_placeholder",
        ) from e
    except ValueError as e:
        raise SourceControlConfigurationError(
            f"Invalid PR template syntax: {e!s}",
            code="pr_template_unsupported_syntax",
        ) from e
