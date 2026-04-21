"""Test design versioning, feedback, and Jira draft linkage columns.

Revision ID: 20260421_0010
Revises: 20260421_0009
Create Date: 2026-04-21

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260421_0010"
down_revision: Union[str, None] = "20260421_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_design_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("artifact_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version_action", sa.String(length=32), nullable=False),
        sa.Column("source_feedback_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["artifact_id"], ["agent_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_version_id"], ["test_design_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "version_number", name="uq_test_design_versions_run_version"),
    )
    op.create_index(
        op.f("ix_test_design_versions_workflow_run_id"),
        "test_design_versions",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_design_versions_artifact_id"),
        "test_design_versions",
        ["artifact_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_design_versions_parent_version_id"),
        "test_design_versions",
        ["parent_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_design_versions_source_feedback_id"),
        "test_design_versions",
        ["source_feedback_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_design_versions_is_current"),
        "test_design_versions",
        ["is_current"],
        unique=False,
    )

    op.create_table(
        "test_design_feedback",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reviewed_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("feedback_text", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(length=256), nullable=False),
        sa.Column("target_scope", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["reviewed_version_id"], ["test_design_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_test_design_feedback_workflow_run_id"),
        "test_design_feedback",
        ["workflow_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_design_feedback_reviewed_version_id"),
        "test_design_feedback",
        ["reviewed_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_test_design_feedback_action_type"),
        "test_design_feedback",
        ["action_type"],
        unique=False,
    )

    op.add_column(
        "jira_generated_test_cases",
        sa.Column("case_index", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "jira_generated_test_cases",
        sa.Column("internal_sync_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jira_generated_test_cases",
        sa.Column("jira_sync_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "jira_generated_test_cases",
        sa.Column("last_sync_error", sa.Text(), nullable=True),
    )
    op.create_index(
        op.f("ix_jira_generated_test_cases_case_index"),
        "jira_generated_test_cases",
        ["case_index"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_generated_test_cases_internal_sync_version"),
        "jira_generated_test_cases",
        ["internal_sync_version"],
        unique=False,
    )
    op.create_index(
        op.f("ix_jira_generated_test_cases_jira_sync_status"),
        "jira_generated_test_cases",
        ["jira_sync_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_jira_generated_test_cases_jira_sync_status"), table_name="jira_generated_test_cases")
    op.drop_index(op.f("ix_jira_generated_test_cases_internal_sync_version"), table_name="jira_generated_test_cases")
    op.drop_index(op.f("ix_jira_generated_test_cases_case_index"), table_name="jira_generated_test_cases")
    op.drop_column("jira_generated_test_cases", "last_sync_error")
    op.drop_column("jira_generated_test_cases", "jira_sync_status")
    op.drop_column("jira_generated_test_cases", "internal_sync_version")
    op.drop_column("jira_generated_test_cases", "case_index")

    op.drop_index(op.f("ix_test_design_feedback_action_type"), table_name="test_design_feedback")
    op.drop_index(op.f("ix_test_design_feedback_reviewed_version_id"), table_name="test_design_feedback")
    op.drop_index(op.f("ix_test_design_feedback_workflow_run_id"), table_name="test_design_feedback")
    op.drop_table("test_design_feedback")

    op.drop_index(op.f("ix_test_design_versions_is_current"), table_name="test_design_versions")
    op.drop_index(op.f("ix_test_design_versions_source_feedback_id"), table_name="test_design_versions")
    op.drop_index(op.f("ix_test_design_versions_parent_version_id"), table_name="test_design_versions")
    op.drop_index(op.f("ix_test_design_versions_artifact_id"), table_name="test_design_versions")
    op.drop_index(op.f("ix_test_design_versions_workflow_run_id"), table_name="test_design_versions")
    op.drop_table("test_design_versions")
