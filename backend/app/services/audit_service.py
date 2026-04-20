"""Append-only audit logging helper."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models.audit_log import AuditLog


def write_audit(
    db: Session,
    *,
    event_type: str,
    actor_type: str,
    actor_id: str,
    workflow_run_id: uuid.UUID | None = None,
    step_name: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        workflow_run_id=workflow_run_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        step_name=step_name,
        entity_type=entity_type,
        entity_id=entity_id,
        event_payload_json=payload,
    )
    db.add(row)
    db.flush()
    return row
