"""AutomationJob failure_analysis_json and repair_result_json.

Revision ID: 20260408_0006
Revises: 20260408_0005
Create Date: 2026-04-08

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260408_0006"
down_revision: Union[str, None] = "20260408_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automation_jobs",
        sa.Column("failure_analysis_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "automation_jobs",
        sa.Column("repair_result_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automation_jobs", "repair_result_json")
    op.drop_column("automation_jobs", "failure_analysis_json")
