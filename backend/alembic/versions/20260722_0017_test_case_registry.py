"""test_case_records table and automation_sessions.test_case_record_id."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260722_0017"
down_revision = "20260721_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_case_records",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("registry_key", sa.String(length=128), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_story_key", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False, server_default="jira"),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("external_url", sa.String(length=1024), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("objective", sa.Text(), nullable=True),
        sa.Column("case_type", sa.String(length=32), nullable=False, server_default="generic"),
        sa.Column("case_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("steps_json", sa.JSON(), nullable=True),
        sa.Column("expected_results_json", sa.JSON(), nullable=True),
        sa.Column("preconditions_json", sa.JSON(), nullable=True),
        sa.Column("assumptions_json", sa.JSON(), nullable=True),
        sa.Column("missing_information_json", sa.JSON(), nullable=True),
        sa.Column("approval_status", sa.String(length=32), nullable=False),
        sa.Column("publication_status", sa.String(length=32), nullable=False),
        sa.Column("publication_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("automation_status", sa.String(length=32), nullable=False),
        sa.Column("automation_session_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("source_artifact_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("test_design_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("provenance_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["automation_session_id"], ["automation_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["agent_artifacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["test_design_version_id"], ["test_design_versions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("registry_key", name="uq_test_case_records_registry_key"),
    )
    op.create_index("ix_test_case_records_workflow_run_id", "test_case_records", ["workflow_run_id"])
    op.create_index("ix_test_case_records_source_story_key", "test_case_records", ["source_story_key"])
    op.create_index("ix_test_case_records_publication_status", "test_case_records", ["publication_status"])
    op.create_index("ix_test_case_records_automation_status", "test_case_records", ["automation_status"])
    op.create_index("ix_test_case_records_external_id", "test_case_records", ["external_id"])

    op.add_column(
        "automation_sessions",
        sa.Column("test_case_record_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_automation_sessions_test_case_record_id",
        "automation_sessions",
        "test_case_records",
        ["test_case_record_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_automation_sessions_test_case_record_id",
        "automation_sessions",
        ["test_case_record_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_automation_sessions_test_case_record_id", table_name="automation_sessions")
    op.drop_constraint("fk_automation_sessions_test_case_record_id", "automation_sessions", type_="foreignkey")
    op.drop_column("automation_sessions", "test_case_record_id")
    op.drop_table("test_case_records")
