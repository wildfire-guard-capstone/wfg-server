# ruff: noqa: E501
"""AI 어시스턴트 · 상황 정정 API(로컬 DB): AI-01 · AI-02 · COR-01 · COR-02 · PROP-05.

어시스턴트는 설정 기본값(ASSISTANT=rules, LLM 없음)으로 돈다 — EXAONE 답 품질은 assistant/eval에서 본다.
"""

from sqlalchemy import select, text

from app.db.models import EventLog, Incident, Proposal
from app.db.models.enums import ProposalTrigger
from app.repositories import assistant as repo

URL = "/api/v1/incidents"


def predict(client, inc, headers):
    r = client.post(
        f"{URL}/{inc['id']}/predictions",
        json={"expected_perimeter_id": inc["current_perimeter_id"]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    op = r.json()["data"]
    op = client.get(f"{URL}/{inc['id']}/operations/{op['id']}", headers=headers).json()["data"]
    assert op["status"] == "SUCCEEDED", op
    return op["result"]


def ask(client, inc, headers, text, **extra):
    return client.post(
        f"{URL}/{inc['id']}/assistant/messages", json={"input_text": text, **extra}, headers=headers
    )


def test_question_is_answered_saved_and_listed(client, login, reported):
    cmd = login("t_cmd", "COMMANDER")
    result = predict(client, reported, cmd)
    r = ask(client, reported, cmd, "지금 대응단계가 뭐야?")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["type"] == "ANSWER"
    msg = data["message"]
    assert (
        msg["proposal_id"] == result["proposal_id"] and msg["question"] == "지금 대응단계가 뭐야?"
    )
    assert msg["answer"] and msg["route"] in ("STATUS", "PROPOSAL", "REGULATION")
    assert msg["sentences"] and msg["sentences"][0]["data_refs"]

    r = ask(client, reported, cmd, "이 항목 판정 이유는?", item_code="s1")
    assert r.json()["data"]["message"]["item_code"] == "S1"

    page = client.get(f"{URL}/{reported['id']}/assistant/messages", headers=cmd).json()["data"]
    assert [m["question"] for m in page["items"]] == [
        "이 항목 판정 이유는?",
        "지금 대응단계가 뭐야?",
    ]
    one = client.get(
        f"{URL}/{reported['id']}/assistant/messages", params={"limit": 1}, headers=cmd
    ).json()["data"]
    assert one["has_more"] and len(one["items"]) == 1


def test_question_before_prediction_and_validation(client, login, reported):
    cmd = login("t_cmd", "COMMANDER")
    msg = ask(client, reported, cmd, "지금 위험구역 마을이 어디야?").json()["data"]["message"]
    assert msg["proposal_id"] is None and "예측" in msg["answer"]
    assert ask(client, reported, cmd, "질문", item_code="X9").status_code == 422
    assert ask(client, reported, cmd, "   ").status_code == 422
    assert ask(client, reported, cmd, "질문", proposal_id=999999999).status_code == 404


def test_roles(client, login, reported):
    view = login("t_view", "VIEWER")
    assert ask(client, reported, view, "헬기 몇 대?").status_code == 403
    r = client.get(f"{URL}/{reported['id']}/corrections", headers=view)
    assert r.status_code == 403


def test_correction_preview_confirm_and_regenerate(client, login, reported, db):
    cmd = login("t_cmd", "COMMANDER")
    first = predict(client, reported, cmd)
    data = ask(client, reported, cmd, "산림청 헬기 2대 추가 투입").json()["data"]
    assert data["type"] == "CORRECTION_PREVIEW", data
    assert data["situation_revision"] == "rev-0" and data["proposal_regenerates"] is True
    items = {c["item"]: (c["before"], c["after"]) for c in data["changes"]}
    assert items == {"helicopter.forest": (0, 2), "helicopter.total": (0, 2)}
    # 확인 전에는 아무것도 저장하지 않는다
    assert db.get(Incident, int(reported["id"])).situation == {}
    assert (
        client.get(f"{URL}/{reported['id']}/assistant/messages", headers=cmd).json()["data"][
            "items"
        ]
        == []
    )

    token = data["confirmation_token"]
    r = client.post(
        f"{URL}/{reported['id']}/corrections", json={"confirmation_token": token}, headers=cmd
    )
    assert r.status_code == 201, r.text
    out = r.json()["data"]
    assert out["correction"]["input_text"] == "산림청 헬기 2대 추가 투입"
    assert out["situation_revision"] == f"rev-{out['correction']['id']}"
    assert out["proposal_stale"] is True and out["proposal_operation"]["kind"] == (
        "PROPOSAL_REGENERATION"
    )
    db.expire_all()
    situation = db.get(Incident, int(reported["id"])).situation
    assert situation["deployed"]["helicopter"] == {"forest": 2, "total": 2}

    op_id = out["proposal_operation"]["id"]
    op = client.get(f"{URL}/{reported['id']}/operations/{op_id}", headers=cmd).json()["data"]
    assert op["status"] == "SUCCEEDED", op
    latest = db.scalar(
        select(Proposal)
        .where(Proposal.incident_id == int(reported["id"]))
        .order_by(Proposal.id.desc())
    )
    assert latest.id == int(op["result"]["proposal_id"]) != int(first["proposal_id"])
    assert latest.trigger is ProposalTrigger.CORRECTION and latest.seq == 2
    assert latest.correction_id == int(out["correction"]["id"])
    assert latest.context["resources"]["helicopter"]["total"] == 2  # 엔진이 정정 값을 읽었다
    inc = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
    assert inc["proposal_stale"] is False

    again = client.post(
        f"{URL}/{reported['id']}/corrections", json={"confirmation_token": token}, headers=cmd
    )
    assert (again.status_code, again.json()["error"]["code"]) == (409, "STALE_SITUATION")
    hist = client.get(f"{URL}/{reported['id']}/corrections", headers=cmd).json()["data"]
    assert [c["id"] for c in hist["items"]] == [out["correction"]["id"]]
    actions = set(
        db.scalars(select(EventLog.action).where(EventLog.incident_id == latest.incident_id))
    )
    assert {"SITUATION_CORRECTED", "PROPOSAL_CREATED"} <= actions

    # 바뀐 상황으로 질문하면 정정 값이 보인다
    msg = ask(client, reported, cmd, "헬기 몇 대 투입됐어?").json()["data"]["message"]
    assert "2대" in msg["answer"]


def test_correction_rejections(client, login, reported):
    cmd = login("t_cmd", "COMMANDER")
    r = ask(client, reported, cmd, "확산대응 2단계 발령됐어")
    assert r.json()["data"]["type"] == "UNSUPPORTED"
    r = ask(client, reported, cmd, "없는마을리 대피 완료")
    assert (r.status_code, r.json()["error"]["code"]) == (422, "TARGET_NOT_FOUND")
    r = ask(client, reported, cmd, "헬기 2대 철수")  # 뺄 미상 헬기가 없음 → 어느 기관인지 묻기
    assert r.json()["data"]["type"] == "UNSUPPORTED" and "어느 기관" in r.json()["data"]["reason"]
    bad = client.post(
        f"{URL}/{reported['id']}/corrections",
        json={"confirmation_token": "not-a-token-at-all"},
        headers=cmd,
    )
    assert bad.json()["error"]["code"] == "INVALID_CONFIRMATION_TOKEN"


def test_helicopter_without_agency_goes_to_unknown(client, login, reported, db):
    """11-10 결정: 기관을 모르는 헬기는 미상 칸 → 엔진 합계에 들어가고, 나중에 기관 칸으로 옮긴다."""
    cmd = login("t_cmd", "COMMANDER")

    def confirm(text):
        data = ask(client, reported, cmd, text).json()["data"]
        assert data["type"] == "CORRECTION_PREVIEW", data
        r = client.post(
            f"{URL}/{reported['id']}/corrections",
            json={"confirmation_token": data["confirmation_token"]},
            headers=cmd,
        )
        assert r.status_code == 201, r.text
        db.expire_all()
        return db.get(Incident, int(reported["id"])).situation["deployed"]["helicopter"]

    assert confirm("헬기 3대 추가") == {"unknown": 3, "total": 3}
    assert confirm("미상 헬기 2대는 산림청이었어") == {"unknown": 1, "forest": 2, "total": 3}


def test_capacity_uses_resource_table(client, login, reported, db):
    """11-10 결정: 상한 = 자원 테이블의 대기 + 이 산불 투입(칸별). 확인안 뒤 자원이 줄면 저장에서 409."""
    cmd = login("t_cmd", "COMMANDER")
    base = db.execute(
        text(
            "INSERT INTO resource_bases (name, org_type, coverage_area_ids)"
            " VALUES ('시험 산림항공관리소', 'forest', '{}') RETURNING id"
        )
    ).scalar_one()
    for name, status in [
        ("시험-1", "AVAILABLE"),
        ("시험-2", "AVAILABLE"),
        ("시험-3", "MAINTENANCE"),
    ]:
        db.execute(
            text(
                "INSERT INTO resources (kind, name, base_id, status)"
                " VALUES ('HELICOPTER', :n, :b, CAST(:s AS resource_status))"
            ),
            {"n": name, "b": base, "s": status},
        )
    db.flush()
    assert repo.capacity(db, int(reported["id"]))["helicopter.forest"] == 2  # 정비 중은 뺌

    over = ask(client, reported, cmd, "산림청 헬기 3대 추가").json()["data"]
    assert over["type"] == "UNSUPPORTED" and "가용 대수(2대" in over["reason"]
    ok = ask(client, reported, cmd, "산림청 헬기 2대 추가").json()["data"]
    assert ok["type"] == "CORRECTION_PREVIEW"
    db.execute(text("UPDATE resources SET status = 'MAINTENANCE' WHERE name = '시험-2'"))
    db.flush()
    r = client.post(
        f"{URL}/{reported['id']}/corrections",
        json={"confirmation_token": ok["confirmation_token"]},
        headers=cmd,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "RESOURCE_CAPACITY_EXCEEDED")


def test_correction_before_prediction_saves_only_situation(client, login, reported, db):
    cmd = login("t_cmd", "COMMANDER")
    data = ask(client, reported, cmd, "진화대 30명 투입").json()["data"]
    assert data["proposal_regenerates"] is False
    r = client.post(
        f"{URL}/{reported['id']}/corrections",
        json={"confirmation_token": data["confirmation_token"]},
        headers=cmd,
    )
    out = r.json()["data"]
    assert out["proposal_operation"] is None and out["proposal_stale"] is False
    db.expire_all()
    crew = db.get(Incident, int(reported["id"])).situation["deployed"]["crew"]
    assert crew == {"special_crew": 30, "total": 30}


def test_ended_incident_allows_questions_only(client, login, reported):
    cmd = login("t_cmd", "COMMANDER")
    data = ask(client, reported, cmd, "산림청 헬기 1대 추가").json()["data"]
    r = client.post(
        f"{URL}/{reported['id']}/end",
        json={"expected_perimeter_id": reported["current_perimeter_id"]},
        headers=cmd,
    )
    assert r.status_code == 200, r.text
    r = ask(client, reported, cmd, "산림청 헬기 1대 추가")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "INCIDENT_ENDED")
    r = client.post(
        f"{URL}/{reported['id']}/corrections",
        json={"confirmation_token": data["confirmation_token"]},
        headers=cmd,
    )
    assert r.json()["error"]["code"] == "INCIDENT_ENDED"
    assert ask(client, reported, cmd, "헬기 몇 대 투입됐어?").status_code == 200


def test_regeneration_endpoint(client, login, reported, db, make_user):
    cmd = login("t_cmd", "COMMANDER")
    result = predict(client, reported, cmd)
    body = {"expected_prediction_id": result["prediction_id"], "situation_revision": "rev-0"}
    url = f"{URL}/{reported['id']}/proposal-regenerations"
    r = client.post(url, json=body, headers=cmd)
    assert r.json()["error"]["code"] == "REGENERATION_NOT_NEEDED"

    # 정정은 저장됐지만 제안서를 못 만든 상황(작업 시작 실패)을 흉내 낸다
    user_id = db.scalar(text("SELECT id FROM users WHERE login_id = 't_cmd'"))
    row = repo.add_correction(
        db,
        incident_id=int(reported["id"]),
        user_id=user_id,
        input_text="진화대 10명 투입",
        changes=[{"target": "deployed", "item": "crew.special_crew", "before": 0, "after": 10}],
    )
    db.execute(
        text(
            "UPDATE situation_corrections SET created_at = now() + interval '1 second' WHERE id = :i"
        ),
        {"i": row.id},
    )
    db.commit()
    assert client.post(url, json=body, headers=cmd).json()["error"]["code"] == "STALE_SITUATION"
    wrong = {**body, "expected_prediction_id": "999999", "situation_revision": f"rev-{row.id}"}
    assert client.post(url, json=wrong, headers=cmd).json()["error"]["code"] == "STALE_PREDICTION"
    r = client.post(url, json={**body, "situation_revision": f"rev-{row.id}"}, headers=cmd)
    assert r.status_code == 202, r.text
    op = client.get(f"{URL}/{reported['id']}/operations/{r.json()['data']['id']}", headers=cmd)
    assert op.json()["data"]["status"] == "SUCCEEDED", op.json()
    latest = db.scalar(
        select(Proposal)
        .where(Proposal.incident_id == int(reported["id"]))
        .order_by(Proposal.id.desc())
    )
    assert latest.trigger is ProposalTrigger.CORRECTION and latest.correction_id == row.id
