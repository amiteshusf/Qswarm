"""Initial Sprint 1 tables.

Revision ID: 20250408_0001
Revises:
Create Date: 2026-04-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20250408_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jira_stories",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("issue_key", sa.String(length=64), nullable=False),
        sa.Column("issue_id", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("issue_type", sa.String(length=128), nullable=True),
        sa.Column("priority", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=128), nullable=True),
        sa.Column("assignee", sa.String(length=256), nullable=True),
        sa.Column("reporter", sa.String(length=256), nullable=True),
        sa.Column("labels_json", sa.JSON(), nullable=True),
        sa.Column("raw_payload_json", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jira_stories_issue_key"), "jira_stories", ["issue_key"], unique=True)

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("jira_story_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("workflow_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_step", sa.String(length=128), nullable=True),
        sa.Column("graph_state_json", sa.JSON(), nullable=True),
        sa.Column("initiated_by", sa.String(length=256), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["jira_story_id"], ["jira_stories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workflow_runs_jira_story_id"), "workflow_runs", ["jira_story_id"], unique=False)
    op.create_index(op.f("ix_workflow_runs_status"), "workflow_runs", ["status"], unique=False)

    op.create_table(
        "agent_artifacts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("agent_name", sa.String(length=128), nullable=False),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_artifacts_workflow_run_id"), "agent_artifacts", ["workflow_run_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_artifacts_artifact_type"), "agent_artifacts", ["artifact_type"], unique=False
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("artifact_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=256), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_by", sa.String(length=256), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["agent_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_approvals_workflow_run_id"), "approvals", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_approvals_artifact_id"), "approvals", ["artifact_id"], unique=False)
    op.create_index(op.f("ix_approvals_status"), "approvals", ["status"], unique=False)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=256), nullable=False),
        sa.Column("step_name", sa.String(length=128), nullable=True),
        sa.Column("entity_type", sa.String(length=128), nullable=True),
        sa.Column("entity_id", sa.String(length=128), nullable=True),
        sa.Column("event_payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_logs_workflow_run_id"), "audit_logs", ["workflow_run_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_event_type"), "audit_logs", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_event_type"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_workflow_run_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_approvals_status"), table_name="approvals")
    op.drop_index(op.f("ix_approvals_artifact_id"), table_name="approvals")
    op.drop_index(op.f("ix_approvals_workflow_run_id"), table_name="approvals")
    op.drop_table("approvals")
    op.drop_index(op.f("ix_agent_artifacts_artifact_type"), table_name="agent_artifacts")
    op.drop_index(op.f("ix_agent_artifacts_workflow_run_id"), table_name="agent_artifacts")
    op.drop_table("agent_artifacts")
    op.drop_index(op.f("ix_workflow_runs_status"), table_name="workflow_runs")
    op.drop_index(op.f("ix_workflow_runs_jira_story_id"), table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index(op.f("ix_jira_stories_issue_key"), table_name="jira_stories")
    op.drop_table("jira_stories")
