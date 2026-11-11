from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import bindparam, text

from tests.integration.conftest import incident_body as body
from tests.integration.conftest import square

URL = "/api/v1/incidents"


def test_report_list_detail_perimeters(client, login, anpyeong):
    rep, cmd = login("t_rep", "REPORTER"), login("t_cmd", "COMMANDER")
    perimeter = {"geometry": square(*anpyeong), "observed_at": "2025-03-22T11:55:00+09:00"}
    payload = body(*anpyeong, initial_perimeter=perimeter)
    r = client.post(URL, json=payload, headers={**rep, "Idempotency-Key": "k-1"})
    assert r.status_code == 201, r.text
    inc = r.json()["data"]
    assert inc["name"] == "의성군 안평면 산불 (2025-03-22)"
    assert inc["status"] == "IN_PROGRESS" and inc["current_perimeter_version"] == 1
    assert inc["needs_reprediction"] and inc["latest_prediction_id"] is None
    assert inc["started_at"] == "2025-03-22T11:24:00+09:00" and "alert_level" not in inc

    listed = client.get(URL, params={"status": "IN_PROGRESS"}, headers=cmd).json()["data"]["items"]
    assert inc["id"] in [i["id"] for i in listed]
    detail = client.get(f"{URL}/{inc['id']}", headers=cmd).json()["data"]
    assert detail["ignition_point"]["coordinates"] == pytest.approx(anpyeong, abs=1e-5)

    pages = client.get(f"{URL}/{inc['id']}/perimeters", headers=cmd).json()["data"]["items"]
    assert [p["version_no"] for p in pages] == [1, 0]
    assert pages[0]["geometry"]["type"] == "MultiPolygon" and pages[0]["area_ha"] > 0
    assert pages[1]["geometry"]["type"] == "Point" and pages[1]["area_ha"] == 0


def test_report_validation(client, login, anpyeong):
    rep = login("t_rep2", "REPORTER")
    r = client.post(URL, json=body(126.97, 37.56), headers=rep)  # 서울
    assert r.status_code == 422 and r.json()["error"]["code"] == "OUTSIDE_JURISDICTION"
    bad = body(*anpyeong)
    bad["ignition_point"] = square(*anpyeong)
    assert client.post(URL, json=bad, headers=rep).json()["error"]["code"] == "INVALID_GEOMETRY"
    naive = body(*anpyeong, reported_at="2025-03-22T11:25:00")
    assert client.post(URL, json=naive, headers=rep).json()["error"]["code"] == "VALIDATION_ERROR"
    alert = client.post(URL, json=body(*anpyeong, alert_level="ATTENTION"), headers=rep)
    assert alert.status_code == 422 and alert.json()["error"]["code"] == "VALIDATION_ERROR"
    viewer = login("t_view2", "VIEWER")
    assert client.post(URL, json=body(*anpyeong), headers=viewer).status_code == 403


def test_end_incident(client, login, anpyeong):
    rep, cmd = login("t_rep3", "REPORTER"), login("t_cmd3", "COMMANDER")
    inc = client.post(URL, json=body(*anpyeong), headers=rep).json()["data"]
    end = f"{URL}/{inc['id']}/end"
    r = client.post(end, json={"expected_perimeter_id": "999999999"}, headers=cmd)
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_PERIMETER"
    r = client.post(end, json={"expected_perimeter_id": inc["current_perimeter_id"]}, headers=cmd)
    assert r.status_code == 200
    ended = r.json()["data"]
    assert ended["status"] == "ENDED" and ended["ended_by"] is not None
    again = client.post(
        end, json={"expected_perimeter_id": inc["current_perimeter_id"]}, headers=cmd
    )
    assert again.json()["error"]["code"] == "INCIDENT_ENDED"


def test_end_incident_releases_linked_resources(client, login, db, anpyeong):
    """INC-04: 종료하면 이 산불에 투입된 자원만 대기로 돌아가고 연결이 풀린다."""
    rep, cmd = login("t_rep5", "REPORTER"), login("t_cmd5", "COMMANDER")
    inc = client.post(URL, json=body(*anpyeong), headers=rep).json()["data"]
    other = client.post(URL, json=body(*anpyeong), headers=rep).json()["data"]
    a, b, c = db.scalars(text("SELECT id FROM resources ORDER BY id LIMIT 3")).all()
    link = "UPDATE resources SET status = 'DEPLOYED', deployed_incident_id = :inc WHERE id = :id"
    for rid, inc_id in ((a, inc["id"]), (b, inc["id"]), (c, other["id"])):
        db.execute(text(link), {"inc": int(inc_id), "id": rid})
    db.flush()

    r = client.post(
        f"{URL}/{inc['id']}/end",
        json={"expected_perimeter_id": inc["current_perimeter_id"]},
        headers=cmd,
    )
    assert r.status_code == 200, r.text
    rows = db.execute(
        text(
            "SELECT id, status::text, deployed_incident_id FROM resources WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"ids": [a, b, c]},
    ).all()
    state = {i: (s, d) for i, s, d in rows}
    assert state[a] == state[b] == ("AVAILABLE", None)
    assert state[c] == ("DEPLOYED", int(other["id"]))  # 다른 산불 자원은 그대로

    logs = db.execute(
        text("SELECT action, details FROM event_logs WHERE incident_id = :i ORDER BY id"),
        {"i": int(inc["id"])},
    ).all()
    released = [d for act, d in logs if act == "RESOURCE_UPDATED"]
    assert [d["resource_id"] for d in released] == [a, b]
    assert all(d["op"] == "released" for d in released)
    ended = [d for act, d in logs if act == "INCIDENT_ENDED"]
    assert ended == [{"released_resource_ids": [a, b]}]


def test_other_jurisdiction_is_hidden(client, login, make_user, db, anpyeong):
    rep = login("t_rep4", "REPORTER")
    inc = client.post(URL, json=body(*anpyeong), headers=rep).json()["data"]
    andong = db.scalar(
        text("SELECT id FROM admin_areas WHERE level = 'SIGUNGU' AND name = '안동시'")
    )
    make_user("t_andong", "COMMANDER", area=andong)
    r = client.post(
        "/api/v1/auth/login",
        json={"login_id": "t_andong", "password": "test-pw-1234", "role": "COMMANDER"},
    )
    other = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    assert client.get(f"{URL}/{inc['id']}", headers=other).status_code == 404
    listed = client.get(URL, headers=other).json()["data"]["items"]
    assert inc["id"] not in [i["id"] for i in listed]


def test_admin_lists_all_incidents_only(client, login, anpyeong):
    rep, admin = login("t_rep6", "REPORTER"), login("t_admin6", "ADMIN")
    inc = client.post(URL, json=body(*anpyeong), headers=rep).json()["data"]
    listed = client.get(URL, params={"status": "IN_PROGRESS"}, headers=admin)
    assert listed.status_code == 200
    assert inc["id"] in [i["id"] for i in listed.json()["data"]["items"]]
    assert client.get(f"{URL}/{inc['id']}", headers=admin).status_code == 403  # 상세는 그대로


def test_report_and_correct_perimeter(client, login, anpyeong):
    rep, cmd = login("t_rep5", "REPORTER"), login("t_cmd5", "COMMANDER")
    inc = client.post(URL, json=body(*anpyeong), headers=rep).json()["data"]
    per = f"{URL}/{inc['id']}/perimeters"
    new = {"geometry": square(*anpyeong), "observed_at": "2025-03-22T13:00:00+09:00"}

    r = client.post(per, json={**new, "expected_perimeter_id": "999999999"}, headers=rep)
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_PERIMETER"
    early = {**new, "observed_at": "2025-03-22T10:00:00+09:00"}
    r = client.post(
        per, json={**early, "expected_perimeter_id": inc["current_perimeter_id"]}, headers=rep
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_OBSERVED_AT"
    assert (
        client.post(
            per, json={**new, "expected_perimeter_id": inc["current_perimeter_id"]}, headers=cmd
        ).status_code
        == 403
    )

    r = client.post(
        per, json={**new, "expected_perimeter_id": inc["current_perimeter_id"]}, headers=rep
    )
    assert r.status_code == 201, r.text
    v1 = r.json()["data"]
    assert v1["version_no"] == 1 and v1["geometry"]["type"] == "MultiPolygon"
    detail = client.get(f"{URL}/{inc['id']}", headers=cmd).json()["data"]
    assert detail["current_perimeter_id"] == v1["id"] and detail["needs_reprediction"]

    v0 = inc["current_perimeter_id"]
    fix = {"geometry": square(*anpyeong, d=0.004), "observed_at": "2025-03-22T13:05:00+09:00"}
    r = client.post(f"{per}/{v0}/corrections", json=fix, headers=rep)
    assert r.status_code == 422 and r.json()["error"]["code"] == "V0_NOT_CORRECTABLE"
    # 고친 화선 시각: 그 앞 화선(v0)보다 이전이면 거절, 5분 넘게 미래면 거절(PER-02와 같게, 11/11)
    for bad in ("2025-03-22T10:00:00+09:00", (datetime.now(UTC) + timedelta(hours=1)).isoformat()):
        r = client.post(
            f"{per}/{v1['id']}/corrections", json={**fix, "observed_at": bad}, headers=rep
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_OBSERVED_AT", bad
    r = client.post(f"{per}/{v1['id']}/corrections", json=fix, headers=rep)
    assert r.status_code == 201, r.text
    v2 = r.json()["data"]
    assert v2["version_no"] == 2
    r = client.post(f"{per}/{v1['id']}/corrections", json=fix, headers=rep)  # 이미 정정됨
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_PERIMETER"

    pages = client.get(per, headers=cmd).json()["data"]["items"]
    assert [(p["version_no"], p["corrected_at"] is None) for p in pages] == [
        (2, True),
        (1, False),
        (0, True),
    ]
    detail = client.get(f"{URL}/{inc['id']}", headers=cmd).json()["data"]
    assert detail["current_perimeter_id"] == v2["id"]

    client.post(f"{URL}/{inc['id']}/end", json={"expected_perimeter_id": v2["id"]}, headers=cmd)
    r = client.post(per, json={**new, "expected_perimeter_id": v2["id"]}, headers=rep)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INCIDENT_ENDED"


def test_same_request_key_reports_once(client, login, anpyeong):
    rep, other = login("t_rep7", "REPORTER"), login("t_rep8", "REPORTER")
    payload = body(*anpyeong)
    first = client.post(URL, json=payload, headers={**rep, "Idempotency-Key": "k-7"})
    again = client.post(URL, json=payload, headers={**rep, "Idempotency-Key": "k-7"})  # 재전송
    assert first.status_code == again.status_code == 201
    assert again.json()["data"]["id"] == first.json()["data"]["id"]

    changed = body(*anpyeong, address="경북 의성군 안평면 다른 곳")
    r = client.post(URL, json=changed, headers={**rep, "Idempotency-Key": "k-7"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    new_key = client.post(URL, json=payload, headers={**rep, "Idempotency-Key": "k-8"})
    other_user = client.post(URL, json=payload, headers={**other, "Idempotency-Key": "k-7"})
    no_key = [client.post(URL, json=payload, headers=rep) for _ in range(2)]
    ids = {first.json()["data"]["id"], new_key.json()["data"]["id"]}
    ids |= {other_user.json()["data"]["id"]} | {r.json()["data"]["id"] for r in no_key}
    assert len(ids) == 5  # 새 번호 · 다른 보고자 · 번호 없음은 모두 새 산불

    too_long = client.post(URL, json=payload, headers={**rep, "Idempotency-Key": "k" * 101})
    assert too_long.status_code == 422
