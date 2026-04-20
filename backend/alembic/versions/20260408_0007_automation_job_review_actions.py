"""Automation job review actions table.

Revision ID: 20260408_0007
Revises: 20260408_0006
Create Date: 2026-04-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260408_0007"
down_revision: Union[str, None] = "20260408_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation_job_review_actions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=256), nullable=False),
        sa.Column("instruction_text", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_job_id"],
            ["automation_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_automation_job_review_actions_automation_job_id"),
        "automation_job_review_actions",
        ["automation_job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_automation_job_review_actions_automation_job_id"),
        table_name="automation_job_review_actions",
    )
    op.drop_table("automation_job_review_actions")
