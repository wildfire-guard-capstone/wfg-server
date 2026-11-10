"""proposal items agent output

제안 항목에 에이전트 출력 칸(state · sections · evidence · input_key)을 더하고,
「구분 하나 이상」 조건과 근거 청크 테이블(citations · evidence_chunks)을 지운다.
근거 청크는 DB에 두지 않고, 항목이 인용한 원문을 evidence에 저장한다(DB 설계 1.3의 8번, 11/9).

Revision ID: 0498c94ec5f1
Revises: b75cf3e3a646
Create Date: 2026-11-09 09:35:06.171696
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0498c94ec5f1"
down_revision: str | None = "b75cf3e3a646"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = "state IN ('ok', 'partial', 'failed', 'reused')"


def _jsonb_array(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'[]'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column(
        "proposal_items",
        sa.Column("state", sa.String(length=10), server_default="ok", nullable=False),
    )
    op.create_check_constraint("item_state", "proposal_items", STATES)
    op.add_column("proposal_items", _jsonb_array("sections"))
    op.add_column("proposal_items", _jsonb_array("evidence"))
    op.add_column("proposal_items", sa.Column("input_key", sa.String(length=16), nullable=True))
    op.drop_constraint("has_category", "proposal_items", type_="check")
    op.drop_table("citations")
    op.drop_table("evidence_chunks")


def downgrade() -> None:
    # 처음 만들 때(f021e7d5b41a)와 같은 모양으로 되돌린다
    op.create_table(
        "evidence_chunks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("doc_name", sa.String(length=200), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(length=200), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("item_codes", postgresql.ARRAY(sa.String(length=10)), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "citations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("item_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("k", sa.SmallInteger(), nullable=False),
        sa.Column("chunk_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("num_nonnulls(item_id, message_id) = 1", name="one_owner"),
        sa.ForeignKeyConstraint(["chunk_id"], ["evidence_chunks.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["proposal_items.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["qa_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "k"),
        sa.UniqueConstraint("message_id", "k"),
    )
    # 구분이 없는 행(failed 항목)이 있으면 되돌리기 전에 지워야 한다
    op.create_check_constraint("has_category", "proposal_items", "cardinality(categories) >= 1")
    op.drop_column("proposal_items", "input_key")
    op.drop_column("proposal_items", "evidence")
    op.drop_column("proposal_items", "sections")
    op.drop_constraint("item_state", "proposal_items", type_="check")
    op.drop_column("proposal_items", "state")
