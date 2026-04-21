"""Jira single draft review issue + processed comment events.

Revision ID: 20260421_0011
Revises: 20260421_0010
Create Date: 2026-04-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260421_0011"
down_revision: Union[str, None] = "20260421_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jira_test_design_review_issues",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("parent_jira_issue_key", sa.String(length=64), nullable=False),
        sa.Column("review_jira_issue_key", sa.String(length=64), nullable=True),
        sa.Column("artifact_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("publish_status", sa.String(length=32), nullable=False),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["agent_artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", name="uq_jira_td_review_issue_run"),
    )
    op.create_index(
        op.f("ix_jira_test_design_review_issues_parent_jira_issue_key"),
        "jira_test_design_review_issues",
        ["parent_jira_issue_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_test_design_review_issues_review_jira_issue_key"),
        "jira_test_design_review_issues",
        ["review_jira_issue_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_test_design_review_issues_publish_status"),
        "jira_test_design_review_issues",
        ["publish_status"],
        unique=False,
    )

    op.create_table(
        "jira_review_comment_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("review_issue_key", sa.String(length=64), nullable=False),
        sa.Column("jira_comment_id", sa.String(length=64), nullable=False),
        sa.Column("author_account_id", sa.String(length=128), nullable=True),
        sa.Column("raw_comment_text", sa.Text(), nullable=False),
        sa.Column("parsed_action_type", sa.String(length=32), nullable=False),
        sa.Column("target_scope", sa.String(length=64), nullable=True),
        sa.Column("reviewed_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("processed_status", sa.String(length=32), nullable=False),
        sa.Column("response_comment_id", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_feedback_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_feedback_id"], ["test_design_feedback.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_version_id"], ["test_design_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "jira_comment_id", name="uq_jira_review_comment_run_comment"),
    )
    op.create_index(
        op.f("ix_jira_review_comment_events_workflow_run_id"),
        "jira_review_comment_events",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_review_comment_events_review_issue_key"),
        "jira_review_comment_events",
        ["review_issue_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_review_comment_events_parsed_action_type"),
        "jira_review_comment_events",
        ["parsed_action_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_review_comment_events_processed_status"),
        "jira_review_comment_events",
        ["processed_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_jira_review_comment_events_processed_status"), table_name="jira_review_comment_events")
    op.drop_index(op.f("ix_jira_review_comment_events_parsed_action_type"), table_name="jira_review_comment_events")
    op.drop_index(op.f("ix_jira_review_comment_events_review_issue_key"), table_name="jira_review_comment_events")
    op.drop_index(op.f("ix_jira_review_comment_events_workflow_run_id"), table_name="jira_review_comment_events")
    op.drop_table("jira_review_comment_events")

    op.drop_index(op.f("ix_jira_test_design_review_issues_publish_status"), table_name="jira_test_design_review_issues")
    op.drop_index(op.f("ix_jira_test_design_review_issues_review_jira_issue_key"), table_name="jira_test_design_review_issues")
    op.drop_index(op.f("ix_jira_test_design_review_issues_parent_jira_issue_key"), table_name="jira_test_design_review_issues")
    op.drop_table("jira_test_design_review_issues")
