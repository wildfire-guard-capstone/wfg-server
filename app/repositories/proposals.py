"""제안서 저장 — proposals 1행 + proposal_items 항목마다 1행(DB 설계 2.4).

엔진 결과(context · detail · 단계)와 작성기 결과(sections · evidence · state · input_key)는
그대로 넣고, 구분 · 본문 · 진화/대피 · 버전만 여기서 정한다.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FirePerimeter, Incident, Prediction, Proposal, ProposalItem
from app.db.models.enums import ItemAxis, ItemCategory, ProposalTrigger, ResponseStage
from app.propose.base import LlmInput, WrittenItem
from app.repositories import event_logs, incidents
from rules.core.stage import LABEL

STAGE_OF = {label: ResponseStage[stage.name] for stage, label in LABEL.items()}  # 1단계 → STAGE1
AXIS_OF = {"S": ItemAxis.SUPPRESSION, "E": ItemAxis.EVACUATION}
Sections = Sequence[dict[str, Any]]
Compared = dict[str, tuple[Any, ...]]  # 항목 코드 → (구분, 본문)


def paragraphs(sections: Sections) -> list[dict[str, Any]]:
    return [b for s in sections for b in s.get("blocks", []) if b.get("type") == "paragraph"]


def categories_of(sections: Sections) -> list[ItemCategory]:
    """문단 구분을 중복 없이 즉시 · 대기 · 협의 · 요청 순서로. 제안이 없으면(failed) 빈 목록."""
    found = {ItemCategory(p["category"]) for p in paragraphs(sections)}
    return [c for c in ItemCategory if c in found]


def text_of(sections: Sections) -> str:
    """제안 문장만: 문단 본문(근거 표기 없음)을 줄바꿈으로. 제안이 없으면 빈 글."""
    return "\n".join(p["body"] for p in paragraphs(sections))


def changed_items(prev: Compared, new: Compared) -> int:
    """직전 버전과 항목 코드끼리 비교: 구분 · 본문이 바뀐 것 + 새로 생긴 것 + 빠진 것."""
    return sum(prev.get(code) != new.get(code) for code in prev.keys() | new.keys())


def compared(items: Sequence[ProposalItem]) -> Compared:
    return {i.item_code: (tuple(i.categories), i.text) for i in items}


def items_of(session: Session, proposal_id: int) -> list[ProposalItem]:
    return list(
        session.scalars(select(ProposalItem).where(ProposalItem.proposal_id == proposal_id))
    )


def next_seq(session: Session, incident_id: int, perimeter_version: int) -> int:
    """같은 산불 · 같은 화선 버전 안에서 다음 순번(1, 2 …)."""
    last = session.scalar(
        select(func.max(Proposal.seq)).where(
            Proposal.incident_id == incident_id, Proposal.perimeter_version == perimeter_version
        )
    )
    return (last or 0) + 1


def save_proposal(
    session: Session,
    *,
    prediction_id: int,
    llm_input: LlmInput,
    written: list[WrittenItem],
    user_id: int,
) -> int:
    """제안서 하나를 넣고 id를 돌려준다. 커밋은 부르는 쪽이 한다(예측과 한 트랜잭션).

    1. 버전 = 예측의 시작 화선 번호(발화점이면 0) . 같은 화선 안 순번
    2. proposals 1행(현재 · P5 단계, 엔진 묶음) + 항목마다 proposal_items 1행
    3. 산불 대응단계 = 이번 현재 기준 단계(예측할 때만 판정, 11/9)
    4. 이벤트 로그: PROPOSAL_CREATED(버전 · 계기 · 바뀐 항목 수), failed 항목마다 ITEM_NO_EVIDENCE
    """
    engine_items = {it["id"]: it for it in llm_input["items"]}
    if [w["id"] for w in written] != list(engine_items):
        raise ValueError("작성기 결과의 항목이 엔진 항목과 다릅니다")
    prediction = session.get(Prediction, prediction_id)
    incident_id = prediction.incident_id
    perimeter_version = session.get(FirePerimeter, prediction.perimeter_id).version_no  # 1
    prev = incidents.latest_proposal(session, incident_id)
    prev_items = compared(items_of(session, prev.id)) if prev else {}
    stage = llm_input["bundles"]["stage"]
    proposal = Proposal(  # 2
        incident_id=incident_id,
        prediction_id=prediction_id,
        perimeter_version=perimeter_version,
        seq=next_seq(session, incident_id, perimeter_version),
        trigger=ProposalTrigger.PREDICTION,
        judged_stage=STAGE_OF[stage["now"]["stage"]],
        predicted_stage=STAGE_OF[stage["p5"]["stage"]],
        context=llm_input["bundles"],
    )
    session.add(proposal)
    session.flush()
    items = [
        ProposalItem(
            proposal_id=proposal.id,
            axis=AXIS_OF[w["id"][0]],
            item_code=w["id"],
            state=w["state"],
            categories=categories_of(w["sections"]),
            sections=w["sections"],
            evidence=w["evidence"],
            text=text_of(w["sections"]),
            detail=engine_items[w["id"]],
            input_key=w.get("input_key"),
        )
        for w in written
    ]
    session.add_all(items)
    session.get(Incident, incident_id).current_stage = proposal.judged_stage  # 3
    details = {  # 4
        "proposal_id": proposal.id,
        "version": f"v{perimeter_version}.{proposal.seq}",
        "trigger": proposal.trigger.name,
        "changed_items": changed_items(prev_items, compared(items)),
    }
    event_logs.add(
        session, "PROPOSAL_CREATED", user_id=user_id, incident_id=incident_id, details=details
    )
    for item in items:
        if item.state == "failed":
            event_logs.add(
                session,
                "ITEM_NO_EVIDENCE",
                user_id=user_id,
                incident_id=incident_id,
                details={"proposal_id": proposal.id, "item_code": item.item_code},
            )
    session.flush()
    return proposal.id
