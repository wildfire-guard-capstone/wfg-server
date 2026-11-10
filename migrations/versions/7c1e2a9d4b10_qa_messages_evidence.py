"""qa messages evidence

AI 질의응답(AI-01)의 답변 근거를 같은 행에 둔다(DB 설계 2.5, 11/9): 인용한 원문(evidence,
제안 항목의 evidence와 같은 모양 + 답변 속 번호 k)과 답변 상세(detail: 경로 · 문장별 출처 ·
찾지 못한 것 · 고른 도구).

Revision ID: 7c1e2a9d4b10
Revises: 784f529a2319
Create Date: 2026-11-09 22:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c1e2a9d4b10"
down_revision: str | None = "784f529a2319"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "qa_messages",
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "qa_messages",
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("qa_messages", "detail")
    op.drop_column("qa_messages", "evidence")
