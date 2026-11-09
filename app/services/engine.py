"""규칙 엔진 돌리기: 예측 하나 + 바탕 데이터 → 엔진 묶음 · LLM 입력.

여기서는 저장하지 않는다. 예측 작업(services.prediction)이 이 결과를 작성기에 넘기고,
제안서 저장(repositories.proposals: proposals.context · proposal_items.detail)까지 한다.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.engine_inputs import build_inputs
from rules.llm_input import build_llm_input


def run_engine(
    session: Session,
    *,
    incident_id: int,
    reader: str,
    prediction_id: int | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """{"bundles", "common", "items"}. 예측을 고르지 않으면 그 산불의 최신 예측."""
    inputs, incident = build_inputs(
        session,
        incident_id=incident_id,
        reader=reader,
        prediction_id=prediction_id,
        generated_at=generated_at,
    )
    return build_llm_input(inputs, incident)
