"""Session ↔ job review/approve state alignment (Sprint 2 control plane)."""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.constants import AutomationJobStatus, AutomationSessionStatus
from app.db.models.automation_job import AutomationJob
from app.services.execution_service import execution_prerequisites_met

ReconcileOutcome = Literal["none", "reconciled", "already_approved"]

# Job statuses that mean "human may approve for PR" (legacy job approve gate).
JOB_STATUSES_APPROVABLE: frozenset[str] = frozenset(
    {AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value}
)

# Job statuses that the session/BFF surfaces as UI ``awaiting_review`` but are not approvable.
JOB_STATUSES_UI_REVIEW_READY_BUT_NOT_APPROVABLE: frozenset[str] = frozenset(
    {
        AutomationJobStatus.AWAITING_HUMAN_INPUT.value,
        AutomationJobStatus.AWAITING_AUTOMATION_APPROVAL.value,
    }
)

# In-flight job statuses that may be left behind while execution_result_json already succeeded.
_STUCK_AFTER_SUCCESS_JOB_STATUSES: frozenset[str] = frozenset(
    {
        AutomationJobStatus.EXECUTING.value,
        AutomationJobStatus.REVISING_AFTER_REVIEW.value,
        AutomationJobStatus.GENERATING_CODE.value,
        AutomationJobStatus.APPLYING_CHANGES.value,
    }
)


def session_status_implies_review_ready(session_status: str) -> bool:
    return session_status == AutomationSessionStatus.AWAITING_REVIEW.value


def reconcile_job_for_session_approve(db: Session, job: AutomationJob) -> ReconcileOutcome:
    """
    Align legacy job status with a successful execution before session approve.

    Returns ``already_approved`` when the job is already past review approval.
  """
    if job.status == AutomationJobStatus.APPROVED_FOR_PR.value:
        return "already_approved"
    if job.status in JOB_STATUSES_APPROVABLE:
        return "none"

    ex = job.execution_result_json if isinstance(job.execution_result_json, dict) else None
    if ex and ex.get("success") and execution_prerequisites_met(job):
        if job.status in _STUCK_AFTER_SUCCESS_JOB_STATUSES:
            job.status = AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value
            job.blocked_reason = None
            db.flush()
            return "reconciled"
    return "none"


def build_session_approve_state_error_message(*, summary: dict[str, Any], job: AutomationJob) -> str:
    """Explain why approve is blocked, including raw job + session enums."""
    session_status = str(summary.get("status") or "")
    job_status = str(job.status or "")
    ui_status = str(summary.get("ui_status") or "")
    parts = [
        "Job cannot be approved for PR readiness",
        f"job_status={job_status!r}",
        f"session_status={session_status!r}",
    ]
    if ui_status:
        parts.append(f"ui_status={ui_status!r}")
    if session_status_implies_review_ready(session_status) and job_status not in JOB_STATUSES_APPROVABLE:
        if job_status in JOB_STATUSES_UI_REVIEW_READY_BUT_NOT_APPROVABLE:
            parts.append(
                "session is review-ready in the UI but the job awaits human input or a different review gate"
            )
        elif job_status == AutomationJobStatus.APPROVED_FOR_PR.value:
            parts.append("job is already approved_for_pr")
        else:
            parts.append(
                f"expected job_status={AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value!r} "
                "after a successful execution round"
            )
    else:
        parts.append(
            f"approval requires job_status={AutomationJobStatus.AWAITING_AUTOMATION_REVIEW.value!r}"
        )
    if job.blocked_reason:
        parts.append(f"blocked_reason={str(job.blocked_reason)[:300]!r}")
    return "; ".join(parts)
