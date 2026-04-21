"""Jira generated draft test cases from Sprint 1 publish.

Revision ID: 20260421_0009
Revises: 20260408_0008
Create Date: 2026-04-21

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260421_0009"
down_revision: Union[str, None] = "20260408_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jira_generated_test_cases",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("parent_jira_issue_key", sa.String(length=64), nullable=False),
        sa.Column("generated_jira_issue_key", sa.String(length=64), nullable=True),
        sa.Column("artifact_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("case_type", sa.String(length=32), nullable=False),
        sa.Column("reviewer_account_id", sa.String(length=128), nullable=True),
        sa.Column("external_system", sa.String(length=32), nullable=False, server_default="jira"),
        sa.Column("publish_status", sa.String(length=32), nullable=False),
        sa.Column("link_status", sa.String(length=32), nullable=False, server_default="skipped"),
        sa.Column(
            "assignment_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_attempted",
        ),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["agent_artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_jira_generated_test_cases_workflow_run_id"),
        "jira_generated_test_cases",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_generated_test_cases_parent_jira_issue_key"),
        "jira_generated_test_cases",
        ["parent_jira_issue_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_generated_test_cases_generated_jira_issue_key"),
        "jira_generated_test_cases",
        ["generated_jira_issue_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_generated_test_cases_artifact_id"),
        "jira_generated_test_cases",
        ["artifact_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_generated_test_cases_publish_status"),
        "jira_generated_test_cases",
        ["publish_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_jira_generated_test_cases_publish_status"), table_name="jira_generated_test_cases")
    op.drop_index(op.f("ix_jira_generated_test_cases_artifact_id"), table_name="jira_generated_test_cases")
    op.drop_index(
        op.f("ix_jira_generated_test_cases_generated_jira_issue_key"),
        table_name="jira_generated_test_cases",
    )
    op.drop_index(
        op.f("ix_jira_generated_test_cases_parent_jira_issue_key"),
        table_name="jira_generated_test_cases",
    )
    op.drop_index(
        op.f("ix_jira_generated_test_cases_workflow_run_id"),
        table_name="jira_generated_test_cases",
    )
    op.drop_table("jira_generated_test_cases")
