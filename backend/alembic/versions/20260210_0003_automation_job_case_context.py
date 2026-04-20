"""AutomationJob case input and context JSON columns.

Revision ID: 20260210_0003
Revises: 20260210_0002
Create Date: 2026-02-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260210_0003"
down_revision: Union[str, None] = "20260210_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "automation_jobs",
        sa.Column("case_input_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "automation_jobs",
        sa.Column("case_spec_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "automation_jobs",
        sa.Column("repo_context_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("automation_jobs", "repo_context_json")
    op.drop_column("automation_jobs", "case_spec_json")
    op.drop_column("automation_jobs", "case_input_json")
