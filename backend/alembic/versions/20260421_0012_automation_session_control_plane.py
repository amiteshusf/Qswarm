"""Sprint 2 automation session control plane (rounds, plan/patch versions, execution, review requests).

Revision ID: 20260421_0012
Revises: 20260421_0011
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260421_0012"
down_revision: Union[str, None] = "20260421_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "automation_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.Column("source_reference", sa.String(length=512), nullable=True),
        sa.Column("automation_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("repo_owner", sa.String(length=256), nullable=True),
        sa.Column("repo_name", sa.String(length=256), nullable=True),
        sa.Column("repo_path", sa.String(length=1024), nullable=True),
        sa.Column("base_branch", sa.String(length=256), nullable=False),
        sa.Column("coding_engine", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("current_round_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approved_case_id", sa.String(length=512), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["automation_job_id"], ["automation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("automation_job_id", name="uq_automation_sessions_job_id"),
    )
    op.create_index(
        op.f("ix_automation_sessions_status"), "automation_sessions", ["status"], unique=False
    )

    op.create_table(
        "automation_revision_rounds",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("started_by", sa.String(length=256), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("instruction_text", sa.Text(), nullable=True),
        sa.Column("target_scope", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_session_id"], ["automation_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "automation_session_id", "round_number", name="uq_automation_revision_round_session_num"
        ),
    )
    op.create_index(
        op.f("ix_automation_revision_rounds_automation_session_id"),
        "automation_revision_rounds",
        ["automation_session_id"],
        unique=False,
    )

    op.create_table(
        "automation_plan_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revision_round_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_session_id"], ["automation_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["revision_round_id"], ["automation_revision_rounds.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "automation_session_id",
            "version_number",
            name="uq_automation_plan_version_session_ver",
        ),
    )
    op.create_index(
        op.f("ix_automation_plan_versions_automation_session_id"),
        "automation_plan_versions",
        ["automation_session_id"],
        unique=False,
    )

    op.create_table(
        "automation_patch_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revision_round_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("patch_json", sa.JSON(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_session_id"], ["automation_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["revision_round_id"], ["automation_revision_rounds.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "automation_session_id",
            "version_number",
            name="uq_automation_patch_version_session_ver",
        ),
    )
    op.create_index(
        op.f("ix_automation_patch_versions_automation_session_id"),
        "automation_patch_versions",
        ["automation_session_id"],
        unique=False,
    )

    op.create_table(
        "automation_execution_attempts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revision_round_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("target_test_file", sa.String(length=1024), nullable=True),
        sa.Column("command_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_session_id"], ["automation_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["revision_round_id"], ["automation_revision_rounds.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "automation_session_id",
            "attempt_number",
            name="uq_automation_execution_attempt_session_num",
        ),
    )
    op.create_index(
        op.f("ix_automation_execution_attempts_automation_session_id"),
        "automation_execution_attempts",
        ["automation_session_id"],
        unique=False,
    )

    op.create_table(
        "automation_review_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revision_round_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_id", sa.String(length=256), nullable=False),
        sa.Column("instruction_text", sa.Text(), nullable=True),
        sa.Column("target_scope", sa.String(length=512), nullable=True),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_session_id"], ["automation_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["revision_round_id"], ["automation_revision_rounds.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_automation_review_requests_automation_session_id"),
        "automation_review_requests",
        ["automation_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_automation_review_requests_automation_session_id"), table_name="automation_review_requests")
    op.drop_table("automation_review_requests")
    op.drop_index(
        op.f("ix_automation_execution_attempts_automation_session_id"),
        table_name="automation_execution_attempts",
    )
    op.drop_table("automation_execution_attempts")
    op.drop_index(
        op.f("ix_automation_patch_versions_automation_session_id"), table_name="automation_patch_versions"
    )
    op.drop_table("automation_patch_versions")
    op.drop_index(
        op.f("ix_automation_plan_versions_automation_session_id"), table_name="automation_plan_versions"
    )
    op.drop_table("automation_plan_versions")
    op.drop_index(
        op.f("ix_automation_revision_rounds_automation_session_id"),
        table_name="automation_revision_rounds",
    )
    op.drop_table("automation_revision_rounds")
    op.drop_index(op.f("ix_automation_sessions_status"), table_name="automation_sessions")
    op.drop_table("automation_sessions")
