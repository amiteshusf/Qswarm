"""Add plan_approved_at to automation_sessions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260721_0016"
down_revision = "20260519_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automation_sessions",
        sa.Column("plan_approved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automation_sessions", "plan_approved_at")
