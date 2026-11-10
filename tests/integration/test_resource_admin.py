"""RES-03 ~ 05 자원 등록 · 수정 · 삭제 · BASE-02 ~ 04 기지 등록 · 수정 · 삭제(전산 관리자)."""

import pytest
from sqlalchemy import text

URL = "/api/v1/admin"
POINT = {"type": "Point", "coordinates": [128.6, 36.37]}


@pytest.fixture
def admin(login):
    return login("t_admin", "ADMIN")


@pytest.fixture
def base(client, admin, uiseong):
    body = {
        "name": "시험 항공관리소",
        "org_type": "forest",
        "geometry": POINT,
        "coverage_area_ids": [str(uiseong)],
    }
    r = client.post(f"{URL}/resource-bases", json=body, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()["data"]


def heli(base, **extra):
    return {"kind": "HELICOPTER", "name": "시험-헬기-01", "base_id": base["id"], **extra}


def test_base_create_update_and_validation(client, admin, base, uiseong, db):
    assert base["coverage_area_ids"] == [str(uiseong)] and base["org_type"] == "forest"
    assert base["geometry"]["type"] == "Point"
    assert base["geometry"]["coordinates"] == pytest.approx(POINT["coordinates"], abs=1e-5)
    assert base["distance_km"] is None and base["jurisdiction"] is None  # 산불 문맥 없음
    dup = client.post(
        f"{URL}/resource-bases",
        json={"name": "시험 항공관리소", "org_type": "fire", "coverage_area_ids": []},
        headers=admin,
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "DUPLICATE_BASE_NAME"
    emd = db.scalar(text("SELECT id FROM admin_areas WHERE level = 'EMD' LIMIT 1"))
    bad = client.post(
        f"{URL}/resource-bases",
        json={"name": "시험 기지 2", "org_type": "local", "coverage_area_ids": [emd, 999999999]},
        headers=admin,
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_COVERAGE"
    line = {"type": "LineString", "coordinates": [[128.6, 36.37], [128.7, 36.4]]}
    r = client.post(
        f"{URL}/resource-bases",
        json={
            "name": "시험 기지 3",
            "org_type": "local",
            "coverage_area_ids": [],
            "geometry": line,
        },
        headers=admin,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_GEOMETRY"

    target = f"{URL}/resource-bases/{base['id']}"
    sido = db.scalar(text("SELECT parent_id FROM admin_areas WHERE id = :i"), {"i": uiseong})
    r = client.patch(
        target, json={"coverage_area_ids": [sido, uiseong, sido], "geometry": None}, headers=admin
    )
    data = r.json()["data"]
    assert r.status_code == 200 and data["geometry"] is None  # 위치 미상
    assert data["coverage_area_ids"] == [str(sido), str(uiseong)]  # 통째로 바꿈, 중복은 뺌
    assert data["name"] == "시험 항공관리소"
    assert client.patch(target, json={}, headers=admin).status_code == 422
    assert client.patch(target, json={"name": None}, headers=admin).status_code == 422
    missing = client.patch(f"{URL}/resource-bases/999999999", json={"name": "x"}, headers=admin)
    assert missing.status_code == 404
    ops = db.scalars(
        text("SELECT details ->> 'op' FROM event_logs WHERE action = 'BASE_UPDATED' ORDER BY id")
    ).all()
    assert ops == ["created", "updated"]


def test_resource_create_and_validation(client, admin, base):
    attrs = {"model": "KA-32", "capacity_l": 3000}
    r = client.post(f"{URL}/resources", json=heli(base, attrs=attrs), headers=admin)
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["status"] == "AVAILABLE" and data["attrs"] == attrs
    assert data["base_id"] == base["id"] and data["deployed_incident_id"] is None

    def code(body):
        resp = client.post(f"{URL}/resources", json=body, headers=admin)
        return resp.status_code, resp.json()["error"]["code"]

    assert code(heli(base)) == (409, "DUPLICATE_RESOURCE_NAME")
    assert code(heli(base, name="시험-헬기-02", base_id="999999999")) == (422, "INVALID_BASE")
    assert code(heli(base, name="시험-헬기-02", attrs={"capacity_l": -1})) == (
        422,
        "VALIDATION_ERROR",
    )
    # 투입이면 산불이 꼭 있어야 하고, 투입이 아니면 산불을 달 수 없다
    assert code(heli(base, name="시험-헬기-02", status="DEPLOYED")) == (422, "VALIDATION_ERROR")
    assert code(heli(base, name="시험-헬기-02", deployed_incident_id="999999999")) == (
        422,
        "VALIDATION_ERROR",
    )
    # 등록한 자원이 조회(RES-01)에 보인다
    listed = client.get("/api/v1/resources", params={"base_id": base["id"]}, headers=admin).json()[
        "data"
    ]["items"]
    assert [x["name"] for x in listed] == ["시험-헬기-01"]


def test_deploying_links_the_incident_and_other_status_unlinks(client, admin, base, reported):
    created = client.post(
        f"{URL}/resources",
        json=heli(base, status="DEPLOYED", deployed_incident_id=reported["id"]),
        headers=admin,
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["status"] == "DEPLOYED" and data["deployed_incident_id"] == reported["id"]
    target = f"{URL}/resources/{data['id']}"
    # 투입 중인 자원의 산불만 비울 수는 없다
    r = client.patch(target, json={"deployed_incident_id": None}, headers=admin)
    assert r.status_code == 422
    r = client.patch(target, json={"status": "MAINTENANCE"}, headers=admin)
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "MAINTENANCE"
    assert r.json()["data"]["deployed_incident_id"] is None  # 연결이 풀림
    # 다시 투입하려면 산불을 같이 보낸다
    assert client.patch(target, json={"status": "DEPLOYED"}, headers=admin).status_code == 422
    r = client.patch(
        target, json={"status": "DEPLOYED", "deployed_incident_id": reported["id"]}, headers=admin
    )
    assert r.status_code == 200 and r.json()["data"]["deployed_incident_id"] == reported["id"]


def test_cannot_deploy_to_an_ended_incident(client, admin, base, reported, db):
    db.execute(
        text("UPDATE incidents SET status = 'ENDED', ended_at = now() WHERE id = :i"),
        {"i": int(reported["id"])},
    )
    r = client.post(
        f"{URL}/resources",
        json=heli(base, status="DEPLOYED", deployed_incident_id=reported["id"]),
        headers=admin,
    )
    assert r.status_code == 422
    assert r.json()["error"]["fields"][0]["field"] == "deployed_incident_id"


def test_resource_update_replaces_attrs_and_checks_names(client, admin, base):
    first = client.post(
        f"{URL}/resources",
        json=heli(base, attrs={"model": "KA-32", "capacity_l": 3000}),
        headers=admin,
    ).json()["data"]
    client.post(f"{URL}/resources", json=heli(base, name="시험-헬기-02"), headers=admin)
    target = f"{URL}/resources/{first['id']}"
    r = client.patch(target, json={"attrs": {"model": "S-64"}}, headers=admin)
    assert r.json()["data"]["attrs"] == {"model": "S-64"}  # 통째로 바뀜(capacity_l 없어짐)
    r = client.patch(target, json={"name": "시험-헬기-02"}, headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_RESOURCE_NAME"
    assert client.patch(target, json={"name": "시험-헬기-01"}, headers=admin).status_code == 200
    r = client.patch(target, json={"base_id": "999999999"}, headers=admin)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_BASE"
    r = client.patch(
        target, json={"kind": "VEHICLE", "attrs": {"vehicle_type": "진화차"}}, headers=admin
    )
    assert r.status_code == 200 and r.json()["data"]["kind"] == "VEHICLE"
    assert client.patch(target, json={}, headers=admin).status_code == 422
    assert (
        client.patch(f"{URL}/resources/999999999", json={"name": "x"}, headers=admin).status_code
        == 404
    )


def test_delete_resource_then_base(client, admin, base, db):
    res = client.post(f"{URL}/resources", json=heli(base), headers=admin).json()["data"]
    in_use = client.delete(f"{URL}/resource-bases/{base['id']}", headers=admin)
    assert in_use.status_code == 409 and in_use.json()["error"]["code"] == "BASE_IN_USE"
    assert client.delete(f"{URL}/resources/{res['id']}", headers=admin).status_code == 204
    assert client.delete(f"{URL}/resources/{res['id']}", headers=admin).status_code == 404
    assert client.delete(f"{URL}/resource-bases/{base['id']}", headers=admin).status_code == 204
    assert client.delete(f"{URL}/resource-bases/{base['id']}", headers=admin).status_code == 404
    assert (
        db.scalar(text("SELECT count(*) FROM resources WHERE id = :i"), {"i": int(res["id"])}) == 0
    )
    ops = db.scalars(
        text(
            "SELECT details ->> 'op' FROM event_logs WHERE action = 'RESOURCE_UPDATED' ORDER BY id"
        )
    ).all()
    assert ops == ["created", "deleted"]


def test_only_admin_manages_resources(client, login, admin, base):
    cmd = login("t_cmd", "COMMANDER")
    for call in (
        client.post(f"{URL}/resources", json=heli(base), headers=cmd),
        client.patch(f"{URL}/resources/1", json={"status": "MAINTENANCE"}, headers=cmd),
        client.delete(f"{URL}/resources/1", headers=cmd),
        client.post(
            f"{URL}/resource-bases",
            json={"name": "x", "org_type": "local", "coverage_area_ids": []},
            headers=cmd,
        ),
        client.patch(f"{URL}/resource-bases/{base['id']}", json={"name": "x"}, headers=cmd),
        client.delete(f"{URL}/resource-bases/{base['id']}", headers=cmd),
    ):
        assert call.status_code == 403 and call.json()["error"]["code"] == "FORBIDDEN_ROLE"
