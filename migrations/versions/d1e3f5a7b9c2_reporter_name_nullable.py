"""incidents.reporter_name nullable — 신고 기관명 · 성명 중 하나만 있어도 보고(QA 11/23)

Revision ID: d1e3f5a7b9c2
Revises: c8d2e4f6a1b3
Create Date: 2026-11-23 15:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e3f5a7b9c2"
down_revision: str | None = "c8d2e4f6a1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("incidents", "reporter_name", existing_type=sa.String(length=50), nullable=True)


def downgrade() -> None:
    # 성명 없이 저장된 보고는 되돌릴 때 「미상」으로 채운다
    op.execute("UPDATE incidents SET reporter_name = '미상' WHERE reporter_name IS NULL")
    op.alter_column(
        "incidents", "reporter_name", existing_type=sa.String(length=50), nullable=False
    )
