"""intake_records — 기관별 접수 기록(INC-02 · INC-03, QA 11/23)

Revision ID: c8d2e4f6a1b3
Revises: a3d5e7f9b1c2
Create Date: 2026-11-23 15:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d2e4f6a1b3"
down_revision: str | None = "a3d5e7f9b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intake_records",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("incident_id", sa.BigInteger(), nullable=False),
        sa.Column("org_name", sa.String(length=100), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=True),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_intake_records_incident_id"), "intake_records", ["incident_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_intake_records_incident_id"), table_name="intake_records")
    op.drop_table("intake_records")
