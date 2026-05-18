"""Repository connections, branch policies, and session code-review requests.

Revision ID: 20260512_0013
Revises: 20260421_0012
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260512_0013"
down_revision: Union[str, None] = "20260421_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repository_connections",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("owner_or_org", sa.String(length=256), nullable=False),
        sa.Column("project_or_workspace", sa.String(length=256), nullable=True),
        sa.Column("repo_name", sa.String(length=256), nullable=False),
        sa.Column("clone_url", sa.String(length=1024), nullable=True),
        sa.Column("default_branch", sa.String(length=256), nullable=False, server_default="main"),
        sa.Column("auth_type", sa.String(length=64), nullable=False, server_default="github_pat_env"),
        sa.Column("credential_reference", sa.String(length=256), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_repository_connections_provider"), "repository_connections", ["provider"], unique=False
    )

    op.create_table(
        "repository_branch_policies",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("repository_connection_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("base_branch_default", sa.String(length=256), nullable=False, server_default="main"),
        sa.Column(
            "branch_naming_pattern",
            sa.String(length=512),
            nullable=False,
            server_default="qswarm/{session_id}",
        ),
        sa.Column("allow_session_override", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("commit_message_template", sa.String(length=512), nullable=True),
        sa.Column("pr_title_template", sa.String(length=512), nullable=True),
        sa.Column("pr_body_template", sa.Text(), nullable=True),
        sa.Column("default_reviewers_json", sa.JSON(), nullable=True),
        sa.Column("default_labels_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["repository_connection_id"], ["repository_connections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_connection_id", name="uq_branch_policy_connection"),
    )

    op.create_table(
        "code_review_requests",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("automation_session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("repository_connection_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source_branch", sa.String(length=512), nullable=False),
        sa.Column("target_branch", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("external_url", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_session_id"], ["automation_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repository_connection_id"], ["repository_connections.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_code_review_requests_automation_session_id"),
        "code_review_requests",
        ["automation_session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_review_requests_repository_connection_id"),
        "code_review_requests",
        ["repository_connection_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_code_review_requests_status"), "code_review_requests", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_code_review_requests_status"), table_name="code_review_requests")
    op.drop_index(op.f("ix_code_review_requests_repository_connection_id"), table_name="code_review_requests")
    op.drop_index(op.f("ix_code_review_requests_automation_session_id"), table_name="code_review_requests")
    op.drop_table("code_review_requests")
    op.drop_table("repository_branch_policies")
    op.drop_index(op.f("ix_repository_connections_provider"), table_name="repository_connections")
    op.drop_table("repository_connections")
