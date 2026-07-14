"""Background execution for automation session revision rounds (DB-queued, poll-based)."""

from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.constants import (
    ActorType,
    AuditEventType,
    AutomationJobStatus,
    AutomationRevisionRoundStatus,
    AutomationRevisionRoundTrigger,
)
from app.db.models.automation_job import AutomationJob
from app.db.models.automation_revision_round import AutomationRevisionRound
from app.db.models.automation_session import AutomationSession
from app.services import audit_service

logger = logging.getLogger(__name__)

_ACTIVE_ROUND_STATUSES = (
    AutomationRevisionRoundStatus.QUEUED.value,
    AutomationRevisionRoundStatus.IN_PROGRESS.value,
)


def session_has_active_round(db: Session, session_id: uuid.UUID) -> bool:
  """True when a round is queued or currently executing for the session."""
  row = db.scalar(
      select(AutomationRevisionRound.id)
      .where(
          AutomationRevisionRound.automation_session_id == session_id,
          AutomationRevisionRound.status.in_(_ACTIVE_ROUND_STATUSES),
      )
      .limit(1)
  )
  return row is not None


def claim_next_queued_round(db: Session) -> AutomationRevisionRound | None:
    """Claim the oldest queued round for background execution."""
    rnd = db.scalar(
        select(AutomationRevisionRound)
        .where(AutomationRevisionRound.status == AutomationRevisionRoundStatus.QUEUED.value)
        .order_by(AutomationRevisionRound.created_at)
        .limit(1)
        .with_for_update()
    )
    if rnd is None:
        return None
    session = db.get(AutomationSession, rnd.automation_session_id)
    job = db.get(AutomationJob, session.automation_job_id) if session and session.automation_job_id else None
    if session is None or job is None:
        rnd.status = AutomationRevisionRoundStatus.FAILED.value
        db.flush()
        return None
    rnd.status = AutomationRevisionRoundStatus.IN_PROGRESS.value
    audit_service.write_audit(
        db,
        event_type=AuditEventType.AUTOMATION_ROUND_STARTED.value,
        actor_type=ActorType.SYSTEM.value,
        actor_id=rnd.started_by[:256],
        workflow_run_id=session.workflow_run_id,
        step_name="automation_round_worker",
        entity_type="automation_revision_round",
        entity_id=str(rnd.id),
        payload={
            "round_number": rnd.round_number,
            "trigger": rnd.trigger_type,
            "claimed_by": "automation_round_worker",
        },
    )
    db.flush()
    return rnd


def execute_automation_round(
    db: Session,
    round_id: uuid.UUID,
    *,
    repository_connection_id: uuid.UUID | None = None,
    raise_on_error: bool = True,
) -> None:
    """Run heavy automation work for a session round (initial or revision)."""
    from app.services.automation_session_service import (
        execute_initial_automation_round,
        execute_revision_automation_round,
    )

    rnd = db.get(AutomationRevisionRound, round_id)
    if rnd is None:
        raise ValueError("round_not_found")
    if rnd.status in (
        AutomationRevisionRoundStatus.COMPLETED.value,
        AutomationRevisionRoundStatus.FAILED.value,
    ):
        return

    try:
        if rnd.trigger_type == AutomationRevisionRoundTrigger.INITIAL.value:
            execute_initial_automation_round(
                db,
                rnd.automation_session_id,
                round_id=rnd.id,
                repository_connection_id=repository_connection_id,
            )
        elif rnd.trigger_type == AutomationRevisionRoundTrigger.REVIEW_REVISION.value:
            execute_revision_automation_round(db, rnd.automation_session_id, round_id=rnd.id)
        else:
            raise ValueError(f"unsupported_round_trigger:{rnd.trigger_type}")
    except Exception:
        if raise_on_error:
            raise
        logger.exception("automation_round_worker_failed", extra={"round_id": str(round_id)})


def run_worker_once(db: Session) -> bool:
    """Claim and execute one queued round. Returns True if work was performed."""
    rnd = claim_next_queued_round(db)
    if rnd is None:
        return False
    session = db.get(AutomationSession, rnd.automation_session_id)
    repo_conn = session.repository_connection_id if session else None
    try:
        execute_automation_round(
            db,
            rnd.id,
            repository_connection_id=repo_conn,
            raise_on_error=False,
        )
    except Exception:
        logger.exception("automation_round_worker_unhandled", extra={"round_id": str(rnd.id)})
    return True


def maybe_run_round_inline(
    db: Session,
    round_id: uuid.UUID,
    *,
    repository_connection_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> None:
    """When configured, run queued round work in-process (tests/local dev)."""
    s = settings or get_settings()
    if not s.qswarm_automation_run_worker_inline:
        return
    execute_automation_round(
        db,
        round_id,
        repository_connection_id=repository_connection_id,
        raise_on_error=True,
    )


def worker_poll_loop(settings: Settings | None = None) -> None:
    """Long-running poll loop for ``python -m app.workers.automation_round_worker``."""
    from app.db.session import SessionLocal

    s = settings or get_settings()
    poll = float(s.qswarm_automation_worker_poll_seconds)
    logger.info("automation_round_worker_started", extra={"poll_seconds": poll})
    while True:
        db = SessionLocal()
        try:
            if run_worker_once(db):
                db.commit()
            else:
                db.rollback()
                time.sleep(poll)
        except Exception:
            db.rollback()
            logger.exception("automation_round_worker_loop_error")
            time.sleep(poll)
        finally:
            db.close()
