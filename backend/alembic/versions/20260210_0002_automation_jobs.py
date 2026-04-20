"""Automation jobs table (Sprint 2).

Revision ID: 20260210_0002
Revises: 20250408_0001
Create Date: 2026-02-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260210_0002"
down_revision: Union[str, None] = "20250408_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("approved_case_id", sa.String(length=512), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("repo_id", sa.String(length=256), nullable=True),
        sa.Column("repo_path", sa.String(length=1024), nullable=True),
        sa.Column("base_branch", sa.String(length=256), nullable=False),
        sa.Column("branch_name", sa.String(length=256), nullable=True),
        sa.Column("requested_by", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("framework_summary_json", sa.JSON(), nullable=True),
        sa.Column("change_plan_json", sa.JSON(), nullable=True),
        sa.Column("final_result_json", sa.JSON(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("latest_attempt_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_automation_jobs_approved_case_id"), "automation_jobs", ["approved_case_id"], unique=False
    )
    op.create_index(
        op.f("ix_automation_jobs_workflow_run_id"), "automation_jobs", ["workflow_run_id"], unique=False
    )
    op.create_index(op.f("ix_automation_jobs_status"), "automation_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_automation_jobs_status"), table_name="automation_jobs")
    op.drop_index(op.f("ix_automation_jobs_workflow_run_id"), table_name="automation_jobs")
    op.drop_index(op.f("ix_automation_jobs_approved_case_id"), table_name="automation_jobs")
    op.drop_table("automation_jobs")
