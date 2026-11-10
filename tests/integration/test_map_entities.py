"""MAP-03 지도 대상 상세 · MAP-04 행정구역 검색."""

import pytest
from sqlalchemy import text

URL = "/api/v1/map/entities"
AREAS = "/api/v1/admin-areas"


def test_village_and_admin_area_detail(client, login, db, uiseong):
    rep = login("t_rep", "REPORTER")
    village_id = db.scalar(text("SELECT id FROM villages ORDER BY id LIMIT 1"))
    r = client.get(f"{URL}/village/{village_id}", headers=rep)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    v = data["entity"]
    assert data["entity_type"] == "village" and v["id"] == str(village_id)
    assert v["geometry"]["type"] == "MultiPolygon" and v["label_point"]["type"] == "Point"
    assert len(v["code"]) == 10 and isinstance(v["admin_area_id"], str)
    assert {"name", "population", "population_month", "pop65_est", "houses_est"} <= set(v)

    area = client.get(f"{URL}/admin_area/{uiseong}", headers=rep).json()["data"]["entity"]
    assert area["name"] == "의성군" and area["level"] == "SIGUNGU"
    assert isinstance(area["parent_id"], str) and isinstance(area["neighbor_ids"], list)
    assert "geometry" not in area  # 행정구역 DTO에는 도형이 없다(경계는 MAP-02)


def test_facility_and_map_feature_detail(client, login, db):
    cmd = login("t_cmd", "COMMANDER")
    facility_id = db.scalar(text("SELECT id FROM facilities ORDER BY id LIMIT 1"))
    feature_id = db.scalar(text("SELECT id FROM map_features ORDER BY id LIMIT 1"))
    if facility_id is None or feature_id is None:
        pytest.skip("시설 · 지도 요소가 적재되지 않은 DB")
    f = client.get(f"{URL}/facility/{facility_id}", headers=cmd).json()["data"]["entity"]
    assert {"type", "subtype", "name", "sigungu_id", "attrs", "geometry"} <= set(f)
    m = client.get(f"{URL}/map_feature/{feature_id}", headers=cmd).json()["data"]["entity"]
    assert {"kind", "name", "attrs", "geometry"} <= set(m) and m["id"] == str(feature_id)


def test_resource_base_detail_is_not_for_reporters(client, login, db, uiseong):
    base_id = db.scalar(
        text(
            "INSERT INTO resource_bases (name, org_type, coverage_area_ids)"
            " VALUES ('시험 기지(위치 미상)', 'local_lease', ARRAY[:a]::bigint[]) RETURNING id"
        ),
        {"a": uiseong},
    )
    cmd = login("t_cmd", "COMMANDER")
    base = client.get(f"{URL}/resource_base/{base_id}", headers=cmd).json()["data"]["entity"]
    assert base["id"] == str(base_id) and base["coverage_area_ids"] == [str(uiseong)]
    assert base["geometry"] is None and base["distance_km"] is None and base["jurisdiction"] is None
    rep = login("t_rep", "REPORTER")
    r = client.get(f"{URL}/resource_base/{base_id}", headers=rep)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN_ROLE"


def test_entity_errors(client, login):
    cmd = login("t_cmd", "COMMANDER")
    r = client.get(f"{URL}/prediction/1", headers=cmd)
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_ENTITY_TYPE"
    assert client.get(f"{URL}/village/999999999", headers=cmd).status_code == 404
    assert client.get(f"{URL}/village/0", headers=cmd).status_code == 422
    assert client.get(f"{URL}/village/1", headers=login("t_admin", "ADMIN")).status_code == 403


def test_admin_areas_are_limited_to_the_jurisdiction(client, login, db, uiseong):
    cmd = login("t_cmd", "COMMANDER")
    sido = db.scalar(text("SELECT parent_id FROM admin_areas WHERE id = :i"), {"i": uiseong})
    emds = client.get(AREAS, params={"level": "EMD", "limit": 100}, headers=cmd).json()["data"]
    assert emds["items"] and {a["parent_id"] for a in emds["items"]} == {str(uiseong)}
    assert "안평면" in [a["name"] for a in emds["items"]]
    item = emds["items"][0]
    assert set(item) == {"id", "level", "code", "name", "parent_id", "neighbor_ids"}
    assert isinstance(item["code"], str) and len(item["code"]) == 10

    assert [
        a["id"]
        for a in client.get(AREAS, params={"level": "SIDO"}, headers=cmd).json()["data"]["items"]
    ] == [str(sido)]
    assert [
        a["id"]
        for a in client.get(AREAS, params={"level": "SIGUNGU"}, headers=cmd).json()["data"]["items"]
    ] == [str(uiseong)]
    found = client.get(AREAS, params={"q": "안평"}, headers=cmd).json()["data"]["items"]
    assert [a["name"] for a in found] == ["안평면"]
    assert client.get(AREAS, params={"q": "%"}, headers=cmd).json()["data"]["items"] == []


def test_admin_areas_paging_and_admin_scope(client, login, db, uiseong):
    admin = login("t_admin", "ADMIN")
    total = db.scalar(text("SELECT count(*) FROM admin_areas"))
    first = client.get(AREAS, params={"limit": 1}, headers=admin).json()["data"]
    assert len(first["items"]) == 1 and first["has_more"] == (total > 1)
    seen = [first["items"][0]["id"]]
    if first["has_more"]:
        nxt = client.get(
            AREAS, params={"limit": 1, "cursor": first["next_cursor"]}, headers=admin
        ).json()["data"]
        assert nxt["items"][0]["id"] not in seen
    children = client.get(AREAS, params={"parent_id": uiseong, "limit": 100}, headers=admin).json()[
        "data"
    ]["items"]
    assert children and all(a["level"] == "EMD" for a in children)
    assert client.get(AREAS, params={"level": "DONG"}, headers=admin).status_code == 422
    assert client.get(AREAS, params={"q": ""}, headers=admin).status_code == 422
    assert client.get(AREAS).status_code == 401
