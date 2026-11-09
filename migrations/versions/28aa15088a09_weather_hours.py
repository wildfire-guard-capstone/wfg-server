"""weather hours

기상청에서 받은 시간별 기상(관측 · 예보)을 저장하는 테이블 하나를 만든다
(Obsidian 05 기상 설계, 11/9).
같은 위치 · 같은 자료 · 같은 발표분은 한 번만 받는다 — 유일 (location, source, issued_at, at).

Revision ID: 28aa15088a09
Revises: 0498c94ec5f1
Create Date: 2026-11-09 22:05:42.179806
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "28aa15088a09"
down_revision: str | None = "0498c94ec5f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = "source IN ('KMA_NCST', 'KMA_ULTRA', 'KMA_VILLAGE', 'ASOS')"


def upgrade() -> None:
    op.create_table(
        "weather_hours",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("location", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wind_dir", sa.REAL(), nullable=True),
        sa.Column("wind_speed", sa.REAL(), nullable=True),
        sa.Column("temp", sa.REAL(), nullable=True),
        sa.Column("humidity", sa.REAL(), nullable=True),
        sa.Column("precip", sa.REAL(), nullable=True),
        sa.Column("precip_text", sa.String(length=20), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(SOURCES, name="weather_source"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location", "source", "issued_at", "at"),
    )


def downgrade() -> None:
    op.drop_table("weather_hours")
