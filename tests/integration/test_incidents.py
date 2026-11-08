import json

import pytest
from sqlalchemy import text

URL = "/api/v1/incidents"


@pytest.fixture
def anpyeong(db) -> list[float]:
    """안평면 안의 한 점 [경도, 위도]."""
    g = db.scalar(
        text(
            "SELECT ST_AsGeoJSON(ST_Transform(ST_PointOnSurface(geom), 4326)) FROM admin_areas"
            " WHERE level = 'EMD' AND name = '안평면'"
        )
    )
    return json.loads(g)["coordinates"]


def body(lon: float, lat: float, **extra) -> dict:
    return {
        "ignition_point": {"type": "Point", "coordinates": [lon, lat]},
        "address": "경북 의성군 안평면 시험",
        "reported_at": "2025-03-22T11:25:00+09:00",
        "started_at": "2025-03-22T11:24:00+09:00",
        "reporter_name": "미상",
        "reporter_phone": "미상",
        **extra,
    }


def square(lon: float, lat: float, d: float = 0.003) -> dict:
    ring = [[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d]]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def test_report_list_detail_perimeters(client, login, anpyeong):
    rep, cmd = login("t_rep", "REPORTER"), login("t_cmd", "COMMANDER")
    perimeter = {"geometry": square(*anpyeong), "observed_at": "2025-03-22T11:55:00+09:00"}
    payload = body(*anpyeong, alert_level="ATTENTION", initial_perimeter=perimeter)
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
