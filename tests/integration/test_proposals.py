"""제안서 저장(로컬 DB): 예측 → 규칙 엔진 → 가짜 작성기 → 저장.

버전 번호 · 항목 행 · 산불 대응단계 · 이벤트 로그를 본다. PRED-01에 잇기 전 단계(2-2).
"""

import json

from sqlalchemy import select

from agent.generate import assemble
from app.db.models import EventLog, Incident, Proposal, ProposalItem
from app.db.models.enums import ProposalTrigger, ResponseStage
from app.predict.synthetic import SyntheticPredictor
from app.propose.fake import FakeWriter
from app.repositories.proposals import AXIS_OF, STAGE_OF, save_proposal, text_of
from app.services.engine import run_engine
from app.services.prediction import run_prediction


def as_json(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def test_save_proposal_rows_stage_and_events(db, make_user, reported):
    user = make_user("t_prop", "COMMANDER")
    incident_id = int(reported["id"])
    pid = run_prediction(
        db, incident_id=incident_id, predictor=SyntheticPredictor(), requested_by=user.id
    )
    llm_input = run_engine(db, incident_id=incident_id, reader="의성군수", prediction_id=pid)
    written = FakeWriter().write(llm_input)
    stage = llm_input["bundles"]["stage"]
    judged = STAGE_OF[stage["now"]["stage"]]
    other = next(s for s in ResponseStage if s is not judged)
    db.get(Incident, incident_id).current_stage = other  # 저장하면 엔진 판정으로 바뀌는지

    first = save_proposal(
        db, prediction_id=pid, llm_input=llm_input, written=written, user_id=user.id
    )
    # 마지막 항목만 failed(통과한 제안 없음)로 바꿔 한 번 더 — 같은 예측이라 순번만 늘어난다
    last = llm_input["items"][-1]
    failed = assemble(last, [], {}, "failed") | {"input_key": None}
    second = save_proposal(
        db,
        prediction_id=pid,
        llm_input=llm_input,
        written=[*written[:-1], failed],
        user_id=user.id,
    )
    db.expire_all()

    p1, p2 = db.get(Proposal, first), db.get(Proposal, second)
    assert (p1.perimeter_version, p1.seq, p2.seq) == (0, 1, 2)
    assert p1.trigger is ProposalTrigger.PREDICTION and p1.prediction_id == pid
    assert p1.judged_stage is judged
    assert p1.predicted_stage is STAGE_OF[stage["p5"]["stage"]]
    assert p1.context == as_json(llm_input["bundles"])
    assert db.get(Incident, incident_id).current_stage is judged

    items = db.scalars(
        select(ProposalItem).where(ProposalItem.proposal_id == first).order_by(ProposalItem.id)
    ).all()
    assert [i.item_code for i in items] == [w["id"] for w in written]
    for item, w in zip(items, written, strict=True):
        assert item.axis is AXIS_OF[item.item_code[0]]
        assert item.state == "ok" and item.categories and item.input_key is None
        assert item.sections == as_json(w["sections"])
        assert item.text == text_of(w["sections"]) and "[테스트]" in item.text
        assert item.evidence[0]["text"].startswith("테스트 원문")
        assert item.detail["id"] == item.item_code

    gone = db.scalar(
        select(ProposalItem).where(
            ProposalItem.proposal_id == second, ProposalItem.item_code == last["id"]
        )
    )
    assert (gone.state, gone.categories, gone.text, gone.evidence) == ("failed", [], "", [])
    assert gone.sections[0]["blocks"][0]["type"] == "failed"

    logs = db.scalars(
        select(EventLog)
        .where(
            EventLog.incident_id == incident_id,
            EventLog.action.in_(["PROPOSAL_CREATED", "ITEM_NO_EVIDENCE"]),
        )
        .order_by(EventLog.id)
    ).all()
    assert [(e.action, e.details) for e in logs] == [
        (
            "PROPOSAL_CREATED",
            {
                "proposal_id": first,
                "version": "v0.1",
                "trigger": "PREDICTION",
                "changed_items": len(written),  # 첫 제안서는 모두 새로
            },
        ),
        (
            "PROPOSAL_CREATED",
            {"proposal_id": second, "version": "v0.2", "trigger": "PREDICTION", "changed_items": 1},
        ),
        ("ITEM_NO_EVIDENCE", {"proposal_id": second, "item_code": last["id"]}),
    ]
