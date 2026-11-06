"""제안 묶음 — 제안서 버전, 항목, 근거 인용, 근거 청크 (DB 설계 2.4)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import created_at, jsonb_object, pg_enum
from app.db.models.enums import ItemAxis, ItemCategory, ProposalTrigger, ResponseStage


class Proposal(Base):
    """제안서 버전 1개. 버전 = ``v{perimeter_version}.{seq}``."""

    __tablename__ = "proposals"
    __table_args__ = (UniqueConstraint("incident_id", "perimeter_version", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"))
    perimeter_version: Mapped[int] = mapped_column(SmallInteger)
    seq: Mapped[int] = mapped_column(SmallInteger)
    trigger: Mapped[ProposalTrigger] = mapped_column(pg_enum(ProposalTrigger, "proposal_trigger"))
    correction_id: Mapped[int | None] = mapped_column(ForeignKey("situation_corrections.id"))
    # Copy of incidents.current_stage at creation time.
    judged_stage: Mapped[ResponseStage] = mapped_column(pg_enum(ResponseStage, "response_stage"))
    predicted_stage: Mapped[ResponseStage] = mapped_column(pg_enum(ResponseStage, "response_stage"))
    created_at: Mapped[datetime] = created_at()


class ProposalItem(Base):
    """제안서에서 발동한 항목 1개. ``detail`` = 규칙 엔진 판정, ``text`` = LLM 문장."""

    __tablename__ = "proposal_items"
    __table_args__ = (
        UniqueConstraint("proposal_id", "item_code"),
        CheckConstraint("cardinality(categories) >= 1", name="has_category"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"))
    axis: Mapped[ItemAxis] = mapped_column(pg_enum(ItemAxis, "item_axis"))
    # String, not ENUM: the item list lives in code constants.
    item_code: Mapped[str] = mapped_column(String(10))
    categories: Mapped[list[ItemCategory]] = mapped_column(
        ARRAY(pg_enum(ItemCategory, "item_category"))
    )
    text: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = jsonb_object()


class Citation(Base):
    """문장 속 근거 번호 [k] 1개. 제안 항목 또는 AI 답변 중 하나에만 붙는다."""

    __tablename__ = "citations"
    __table_args__ = (
        CheckConstraint("num_nonnulls(item_id, message_id) = 1", name="one_owner"),
        UniqueConstraint("item_id", "k"),
        UniqueConstraint("message_id", "k"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("proposal_items.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("qa_messages.id"))
    k: Mapped[int] = mapped_column(SmallInteger)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("evidence_chunks.id"))


class EvidenceChunk(Base):
    """매뉴얼 · 법령 · 규정 청크 1개. 다시 청킹하면 새 행을 넣고 옛 행은 ``retired_at``."""

    __tablename__ = "evidence_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # e.g. SM-p073-01 — never renamed once assigned.
    code: Mapped[str] = mapped_column(String(50), unique=True)
    doc_name: Mapped[str] = mapped_column(String(200))
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    item_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String(10)))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
