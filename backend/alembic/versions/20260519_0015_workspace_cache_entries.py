"""Workspace cache entries for durable hosted create-pr (short-lived active repo per session).

Revision ID: 20260519_0015
Revises: 20260518_0014
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260519_0015"
down_revision: Union[str, None] = "20260518_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspace_cache_entries",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("automation_session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("repository_connection_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("workspace_path", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["automation_session_id"],
            ["automation_sessions.id"],
            name="fk_workspace_cache_entries_automation_session_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_connection_id"],
            ["repository_connections.id"],
            name="fk_workspace_cache_entries_repository_connection_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_workspace_cache_entries_session_id",
        "workspace_cache_entries",
        ["automation_session_id"],
    )
    op.create_index(
        "ix_workspace_cache_entries_status_expires",
        "workspace_cache_entries",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_cache_entries_status_expires", table_name="workspace_cache_entries")
    op.drop_index("ix_workspace_cache_entries_session_id", table_name="workspace_cache_entries")
    op.drop_table("workspace_cache_entries")
