"""AutomationJob generated_patch_json column.

Revision ID: 20260408_0004
Revises: 20260210_0003
Create Date: 2026-04-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260408_0004"
down_revision: Union[str, None] = "20260210_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automation_jobs",
        sa.Column("generated_patch_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automation_jobs", "generated_patch_json")
