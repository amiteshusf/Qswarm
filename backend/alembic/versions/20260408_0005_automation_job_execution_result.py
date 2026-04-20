"""AutomationJob execution_result_json column.

Revision ID: 20260408_0005
Revises: 20260408_0004
Create Date: 2026-04-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260408_0005"
down_revision: Union[str, None] = "20260408_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automation_jobs",
        sa.Column("execution_result_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automation_jobs", "execution_result_json")
