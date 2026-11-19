"""admin villages

행정리(조례 「의성군 이장 정수 조례」 별표) — 법정리(villages) 아래 단위. 회관 위치(hall_point)와
회관에서 시작하는 K-평균으로 잡은 마을 구역(geom)은 찾은 곳만 둔다(11/19).

Revision ID: a3d5e7f9b1c2
Revises: 7c1e2a9d4b10
Create Date: 2026-11-19 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "a3d5e7f9b1c2"
down_revision: str | None = "7c1e2a9d4b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _geom(geometry_type: str) -> Geometry:
    return Geometry(
        geometry_type=geometry_type,
        srid=5179,
        dimension=2,
        spatial_index=False,
        from_text="ST_GeomFromEWKT",
        name="geometry",
    )


def upgrade() -> None:
    op.create_geospatial_table(
        "admin_villages",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("village_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("hall_name", sa.String(length=100), nullable=True),
        sa.Column("hall_point", _geom("POINT"), nullable=True),
        sa.Column("hall_source", sa.String(length=200), nullable=True),
        sa.Column("geom", _geom("MULTIPOLYGON"), nullable=True),
        sa.Column("houses_est", sa.Integer(), nullable=True),
        sa.Column(
            "attrs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["village_id"], ["villages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("village_id", "name"),
    )
    for column in ("hall_point", "geom"):
        op.create_geospatial_index(
            f"idx_admin_villages_{column}",
            "admin_villages",
            [column],
            unique=False,
            postgresql_using="gist",
            postgresql_ops={},
        )


def downgrade() -> None:
    for column in ("geom", "hall_point"):
        op.drop_geospatial_index(
            f"idx_admin_villages_{column}",
            table_name="admin_villages",
            postgresql_using="gist",
            column_name=column,
        )
    op.drop_geospatial_table("admin_villages")
