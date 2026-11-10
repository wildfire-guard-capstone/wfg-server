"""제안 묶음 — 제안서 버전, 항목 (DB 설계 2.4).

근거 청크 테이블은 두지 않는다(DB 설계 1.3의 8번, 11/9): 항목이 인용한 원문은
``proposal_items.evidence``에 그 회차 그대로 저장한다.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import created_at, jsonb_array, jsonb_object, pg_enum
from app.db.models.enums import ItemAxis, ItemCategory, ProposalTrigger, ResponseStage

ITEM_STATES = ("ok", "partial", "failed", "reused")


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
    # Rule engine output for this version (rules.llm_input "bundles": 16 topic blocks), saved
    # once so items, the AI assistant and later reviews see the same facts.
    context: Mapped[dict[str, Any]] = jsonb_object()
    created_at: Mapped[datetime] = created_at()


class ProposalItem(Base):
    """제안서에서 발동한 항목 1개(S1~E6 같은 모양) — 에이전트 출력을 거의 그대로 저장.

    ``sections`` = 화면 배치(문단 · 목록 · 표 · ※), ``evidence`` = 인용한 원문,
    ``text`` = 제안 문장만, ``detail`` = 규칙 엔진 항목 입력.
    """

    __tablename__ = "proposal_items"
    __table_args__ = (
        UniqueConstraint("proposal_id", "item_code"),
        CheckConstraint(f"state IN {ITEM_STATES}", name="item_state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"))
    axis: Mapped[ItemAxis] = mapped_column(pg_enum(ItemAxis, "item_axis"))
    # String, not ENUM: the item list lives in code constants.
    item_code: Mapped[str] = mapped_column(String(10))
    # ok(생성) · partial(일부 생성) · failed(통과한 제안 없음) · reused(직전 회차 그대로)
    state: Mapped[str] = mapped_column(String(10), server_default="ok")
    # Paragraph categories; empty when the item failed.
    categories: Mapped[list[ItemCategory]] = mapped_column(
        ARRAY(pg_enum(ItemCategory, "item_category"))
    )
    sections: Mapped[list[dict[str, Any]]] = jsonb_array()
    evidence: Mapped[list[dict[str, Any]]] = jsonb_array()
    text: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = jsonb_object()
    # Agent input fingerprint: the next round reuses this item when it is unchanged.
    input_key: Mapped[str | None] = mapped_column(String(16))
