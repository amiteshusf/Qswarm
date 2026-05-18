"""Optional repository_connection_id on automation_sessions for hosted workspace materialization.

Revision ID: 20260518_0014
Revises: 20260512_0013
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260518_0014"
down_revision: Union[str, None] = "20260512_0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automation_sessions",
        sa.Column("repository_connection_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_automation_sessions_repository_connection_id",
        "automation_sessions",
        "repository_connections",
        ["repository_connection_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_automation_sessions_repository_connection_id",
        "automation_sessions",
        type_="foreignkey",
    )
    op.drop_column("automation_sessions", "repository_connection_id")
