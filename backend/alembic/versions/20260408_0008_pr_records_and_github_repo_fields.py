"""PR records table and automation_jobs GitHub repo fields.

Revision ID: 20260408_0008
Revises: 20260408_0007
Create Date: 2026-04-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260408_0008"
down_revision: Union[str, None] = "20260408_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("automation_jobs", sa.Column("repo_owner", sa.String(length=256), nullable=True))
    op.add_column("automation_jobs", sa.Column("repo_name", sa.String(length=256), nullable=True))

    op.create_table(
        "pr_records",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("repo_owner", sa.String(length=256), nullable=False),
        sa.Column("repo_name", sa.String(length=256), nullable=False),
        sa.Column("base_branch", sa.String(length=256), nullable=False),
        sa.Column("branch_name", sa.String(length=512), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("refresh_status", sa.String(length=64), nullable=True),
        sa.Column("refresh_notes_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["automation_job_id"], ["automation_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pr_records_automation_job_id"), "pr_records", ["automation_job_id"], unique=False)
    op.create_index(op.f("ix_pr_records_status"), "pr_records", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_pr_records_status"), table_name="pr_records")
    op.drop_index(op.f("ix_pr_records_automation_job_id"), table_name="pr_records")
    op.drop_table("pr_records")
    op.drop_column("automation_jobs", "repo_name")
    op.drop_column("automation_jobs", "repo_owner")
