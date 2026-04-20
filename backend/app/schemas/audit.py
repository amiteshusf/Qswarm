"""Audit log API schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    workflow_run_id: uuid.UUID | None
    event_type: str
    actor_type: str
    actor_id: str
    step_name: str | None
    entity_type: str | None
    entity_id: str | None
    event_payload_json: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
