from sqlalchemy import text

from app.services import operations

URL = "/api/v1/incidents"


def run(client, inc, headers):
    r = client.post(
        f"{URL}/{inc['id']}/predictions",
        json={"expected_perimeter_id": inc["current_perimeter_id"]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    op = r.json()["data"]
    return client.get(f"{URL}/{inc['id']}/operations/{op['id']}", headers=headers).json()["data"]


def test_predict_then_read_prediction_slices_weather(client, login, reported):
    cmd, view = login("t_cmd", "COMMANDER"), login("t_view", "VIEWER")
    op = run(client, reported, cmd)
    assert op["status"] == "SUCCEEDED" and op["result"]["proposal_id"] is None
    pid = op["result"]["prediction_id"]
    base = f"{URL}/{reported['id']}/predictions/{pid}"

    pred = client.get(base, headers=view).json()["data"]
    assert pred["perimeter_version"] == 0 and pred["base_at"] == "2025-03-22T11:24:00+09:00"
    assert [s["hour"] for s in pred["slices"]] == list(range(1, 9))
    assert pred["slices"][0]["at"] == "2025-03-22T12:24:00+09:00"
    areas = [s["area_ha"] for s in pred["slices"]]
    assert areas == sorted(areas) and areas[0] > 0

    fc = client.get(f"{base}/slices", headers=view).json()["data"]
    assert len(fc["features"]) == 8 and fc["features"][0]["geometry"]["type"] == "MultiPolygon"
    one = client.get(f"{base}/slices", params={"hour": 5}, headers=view).json()["data"]
    assert [f["properties"]["hour"] for f in one["features"]] == [5]

    weather = client.get(f"{base}/weather", headers=view).json()["data"]
    assert weather["source"] == "SYNTHETIC" and len(weather["hours"]) == 9

    detail = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
    assert detail["latest_prediction_id"] == pid and detail["needs_reprediction"] is False


def test_predict_guards(client, login, reported):
    cmd = login("t_cmd2", "COMMANDER")
    preds = f"{URL}/{reported['id']}/predictions"
    r = client.post(preds, json={"expected_perimeter_id": "999999999"}, headers=cmd)
    assert r.json()["error"]["code"] == "STALE_PERIMETER"
    view = login("t_view2", "VIEWER")
    expected = {"expected_perimeter_id": reported["current_perimeter_id"]}
    assert client.post(preds, json=expected, headers=view).status_code == 403

    operations.start("PREDICTION", int(reported["id"]), 0)  # 다른 계산이 도는 중
    assert client.post(preds, json=expected, headers=cmd).json()["error"]["code"] == (
        "PREDICTION_RUNNING"
    )
    end = client.post(f"{URL}/{reported['id']}/end", json=expected, headers=cmd)
    assert end.json()["error"]["code"] == "RESOURCE_BUSY"
    operations.clear()
    assert client.post(f"{URL}/{reported['id']}/end", json=expected, headers=cmd).status_code == 200
    assert client.post(preds, json=expected, headers=cmd).json()["error"]["code"] == (
        "INCIDENT_ENDED"
    )


def test_failed_prediction_keeps_nothing(client, login, reported, db, monkeypatch):
    class Broken:
        name = "broken"

        def predict(self, request):
            raise ValueError("P1~P8이 없습니다")

    monkeypatch.setattr("app.services.prediction.predictor_from_settings", lambda: Broken())
    cmd = login("t_cmd3", "COMMANDER")
    op = run(client, reported, cmd)
    assert op["status"] == "FAILED" and op["error"]["code"] == "PREDICTION_INVALID"
    n = db.scalar(
        text("SELECT count(*) FROM predictions WHERE incident_id = :i"), {"i": int(reported["id"])}
    )
    assert n == 0
    failed = db.scalar(
        text(
            "SELECT count(*) FROM event_logs"
            " WHERE incident_id = :i AND action = 'PREDICTION_FAILED'"
        ),
        {"i": int(reported["id"])},
    )
    assert failed == 1
