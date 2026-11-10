"""AI 어시스턴트 · 상황 정정 요청 본문(명세 AI-01 · COR-01 · PROP-05).

응답은 서비스(app/services/assistant.py)가 명세 DTO 꼴의 dict로 만든다: ANSWER(QaMessage) ·
CORRECTION_PREVIEW · UNSUPPORTED, Correction.
"""

import re

from pydantic import Field, field_validator

from app.schemas.common import In

ITEM_CODE = re.compile(r"^[SE][1-7]$")


class AssistantMessageIn(In):
    input_text: str = Field(min_length=1, max_length=2000)
    proposal_id: int | None = Field(None, gt=0)  # 질문할 때 보고 있던 제안서(없으면 최신)
    item_code: str | None = Field(None, max_length=10)  # 「이 항목 질문」

    @field_validator("input_text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("빈 입력")
        return v

    @field_validator("item_code")
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if not ITEM_CODE.match(v):
            raise ValueError("항목 코드는 S1~S7 · E1~E7")
        return v


class CorrectionConfirmIn(In):
    confirmation_token: str = Field(min_length=10, max_length=20000)


class RegenerationIn(In):
    expected_prediction_id: int = Field(gt=0)
    situation_revision: str = Field(min_length=1, max_length=50)
