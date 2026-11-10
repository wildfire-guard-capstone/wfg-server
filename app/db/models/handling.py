"""제안 다루기 묶음 — 상황 정정과 AI 질의응답 (DB 설계 2.5)."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import created_at, jsonb_array, jsonb_object


class SituationCorrection(Base):
    """확인 카드에서 「확인」한 정정 1건. 정정 1건 = 제안서 새 버전 1개."""

    __tablename__ = "situation_corrections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    input_text: Mapped[str] = mapped_column(Text)
    # [{"target", "target_id", "item", "before", "after"}, ...]
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()


class QaMessage(Base):
    """질문 1개와 그 답변. 상황과 제안을 바꾸지 않는다."""

    __tablename__ = "qa_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Proposal on screen when asked; NULL before the first prediction.
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("proposals.id"))
    item_code: Mapped[str | None] = mapped_column(String(10))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    # Cited source text, same shape as proposal_items.evidence plus k (the [k] in the answer).
    evidence: Mapped[list[dict[str, Any]]] = jsonb_array()
    # route · sentences(문장별 근거 번호 · 상황 값 출처) · insufficient · tools · no_evidence
    detail: Mapped[dict[str, Any]] = jsonb_object()
    created_at: Mapped[datetime] = created_at()
