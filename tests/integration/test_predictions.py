from datetime import datetime

from sqlalchemy import text

from app.services import operations
from app.weather import WeatherUnavailable

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
    assert op["status"] == "SUCCEEDED" and op["result"]["proposal_id"]
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
    # 기상은 설정 WEATHER(기본 fixed)에서 받아 예측에 쓴 값 — 다섯 값 모두
    assert weather["source"] == "FIXED" and len(weather["hours"]) == 9
    assert weather["hours"][0]["temp"] == 15.0 and weather["hours"][0]["kind"] == "고정"

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

    monkeypatch.setattr(
        "app.services.prediction.predictor_from_settings", lambda mode=None: Broken()
    )
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


def test_weather_failure_keeps_nothing(client, login, reported, db, monkeypatch):
    """기상을 못 받으면 요청이 503 WEATHER_UNAVAILABLE(명세 v0.4) — 작업 · 예측은 만들지 않는다."""

    class NoWeather:
        name = "down"

        def fetch(self, request):
            raise WeatherUnavailable("기상청 연결 실패")

    monkeypatch.setattr("app.routers.predictions.provider_from_settings", lambda: NoWeather())
    cmd = login("t_cmd6", "COMMANDER")
    r = client.post(
        f"{URL}/{reported['id']}/predictions",
        json={"expected_perimeter_id": reported["current_perimeter_id"]},
        headers=cmd,
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "WEATHER_UNAVAILABLE"
    assert "기상청 연결 실패" in r.json()["error"]["message"]
    assert not operations.is_running(int(reported["id"]))  # 작업을 시작하지 않음
    n = db.scalar(
        text("SELECT count(*) FROM predictions WHERE incident_id = :i"), {"i": int(reported["id"])}
    )
    assert n == 0


def test_elmfire_weather_failure_is_503_too(client, login, reported, db, monkeypatch):
    """ELMFIRE 기상 입력을 못 만들면(관측소 3곳 미만 · 덱에 없는 시각) 같은 503.
    다른 기상으로 대신하지 않고 작업도 만들지 않는다."""
    from types import SimpleNamespace

    def no_input(*args, **kwargs):
        raise WeatherUnavailable("기상 격자에 쓸 관측소가 2곳뿐입니다(최소 3)")

    monkeypatch.setattr(
        "app.routers.predictions.get_settings", lambda: SimpleNamespace(predictor="elmfire")
    )
    monkeypatch.setattr(
        "app.routers.predictions.elmfire_source_from_settings",
        lambda name=None: SimpleNamespace(name="stations", uses_server_weather=False),
    )
    monkeypatch.setattr("app.routers.predictions.build_elmfire_weather", no_input)
    cmd = login("t_cmd7", "COMMANDER")
    r = client.post(
        f"{URL}/{reported['id']}/predictions",
        json={"expected_perimeter_id": reported["current_perimeter_id"]},
        headers=cmd,
    )
    assert r.status_code == 503 and r.json()["error"]["code"] == "WEATHER_UNAVAILABLE"
    assert "관측소가 2곳" in r.json()["error"]["message"]
    assert not operations.is_running(int(reported["id"]))
    n = db.scalar(
        text("SELECT count(*) FROM predictions WHERE incident_id = :i"), {"i": int(reported["id"])}
    )
    assert n == 0


def test_mode_demo_elmfire_runs_elmfire_and_records_mode(client, login, reported, db, monkeypatch):
    """mode=DEMO_ELMFIRE(시연 1): 기본이 가짜 예측이어도 ELMFIRE로 돌고, 쓴 모드를 남긴다.

    덱 · worker 대신 가짜 기상 입력 · 가짜 ELMFIRE를 꽂는다(흐름 · 기록만 본다).
    """
    from types import SimpleNamespace

    import numpy as np

    from app.predict.base import PredictionResult
    from app.weather.base import KST, WeatherRequest
    from app.weather.elmfire_input import ElmfireWeatherInput, floor_hour
    from app.weather.fixed import FixedWeather

    calls = {}

    def source(name=None):
        calls["source"] = name
        return SimpleNamespace(name=name, uses_server_weather=True)

    def build(db_, incident_id, src, server):
        calls["server"] = server
        base_at = datetime(2025, 3, 22, 11, 24, tzinfo=KST)
        weather = FixedWeather(wind_ms=4.0).fetch(WeatherRequest((36.36, 128.60), base_at))
        weather = {**weather, "source": "AWS_OBS"}
        grids = {k: np.zeros((9, 2, 2), "float32") for k in ("ws", "wd", "m1", "m10", "m100")}
        return ElmfireWeatherInput(floor_hour(base_at), grids, weather, {"source": "deck"})

    class FakeElmfire:
        name = "elmfire"

        def predict(self, req):
            slices = [req.start.buffer(50 * h) for h in range(1, 9)]
            return PredictionResult(slices, req.elmfire_weather.weather, "elmfire", {"wall_s": 1})

    asos = SimpleNamespace(name="asos")
    monkeypatch.setattr("app.routers.predictions.elmfire_source_from_settings", source)
    monkeypatch.setattr("app.routers.predictions.build_elmfire_weather", build)
    monkeypatch.setattr("app.routers.predictions.asos_provider_from_settings", lambda: asos)
    monkeypatch.setattr(
        "app.services.prediction.elmfire_predictor_from_settings", lambda: FakeElmfire()
    )
    cmd = login("t_cmd8", "COMMANDER")
    r = client.post(
        f"{URL}/{reported['id']}/predictions",
        json={"expected_perimeter_id": reported["current_perimeter_id"], "mode": "DEMO_ELMFIRE"},
        headers=cmd,
    )
    assert r.status_code == 202, r.text
    op_id = r.json()["data"]["id"]
    op = client.get(f"{URL}/{reported['id']}/operations/{op_id}", headers=cmd).json()["data"]
    assert op["status"] == "SUCCEEDED", op
    assert op["result"]["mode"] == "DEMO_ELMFIRE" and op["result"]["predictor"] == "elmfire"
    assert calls == {"source": "deck", "server": asos}  # 시연 1 = 덱 기상 + 지상관측
    pid = op["result"]["prediction_id"]
    w = client.get(f"{URL}/{reported['id']}/predictions/{pid}/weather", headers=cmd).json()["data"]
    assert w["source"] == "AWS_OBS"
    details = db.scalar(
        text(
            "SELECT details FROM event_logs WHERE action = 'PREDICTION_CREATED'"
            " AND (details->>'prediction_id')::int = :p"
        ),
        {"p": int(pid)},
    )
    assert details["mode"] == "DEMO_ELMFIRE" and details["predictor"] == "elmfire"

    bad = client.post(
        f"{URL}/{reported['id']}/predictions",
        json={"expected_perimeter_id": reported["current_perimeter_id"], "mode": "DEMO_REPLAY"},
        headers=cmd,
    )
    assert bad.status_code == 422  # 시연 2는 아직 없음


def test_predict_also_saves_proposal_and_stage(client, login, reported, db):
    """예측 → 규칙 엔진 → 가짜 작성기 → 제안서 저장까지 한 작업으로. 산불 대응단계 = 엔진 판정."""
    cmd = login("t_cmd5", "COMMANDER")
    op = run(client, reported, cmd)
    assert op["status"] == "SUCCEEDED", op
    pid, proposal_id = int(op["result"]["prediction_id"]), int(op["result"]["proposal_id"])
    row = db.execute(
        text(
            "SELECT prediction_id, perimeter_version, seq, judged_stage FROM proposals"
            " WHERE id = :p"
        ),
        {"p": proposal_id},
    ).one()
    assert (row.prediction_id, row.perimeter_version, row.seq) == (pid, 0, 1)
    states = db.scalars(
        text("SELECT state FROM proposal_items WHERE proposal_id = :p"), {"p": proposal_id}
    ).all()
    assert states and set(states) == {"ok"}
    detail = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
    assert detail["current_stage"] == row.judged_stage
    created = db.scalar(
        text(
            "SELECT count(*) FROM event_logs WHERE incident_id = :i AND action = 'PROPOSAL_CREATED'"
        ),
        {"i": int(reported["id"])},
    )
    assert created == 1


def test_failed_proposal_keeps_neither_prediction_nor_proposal(
    client, login, reported, db, monkeypatch
):
    class Broken:
        name = "broken"

        def write(self, llm_input):
            raise RuntimeError("GPU 서버에 연결할 수 없음")

    monkeypatch.setattr("app.services.prediction.writer_from_settings", lambda: Broken())
    cmd = login("t_cmd6", "COMMANDER")
    op = run(client, reported, cmd)
    assert op["status"] == "FAILED" and op["error"]["code"] == "PROPOSAL_FAILED"
    params = {"i": int(reported["id"])}
    for table in ("predictions", "proposals"):
        sql = f"SELECT count(*) FROM {table} WHERE incident_id = :i"
        assert db.scalar(text(sql), params) == 0, table
    detail = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
    assert detail["current_stage"] == "INITIAL" and detail["latest_prediction_id"] is None


def test_risk(client, login, reported):
    """격자 없이(RISK_GRID_DIR 비움): 기상 4개 · 취약계층 · 소방서 · 규모는 계산, 나머지는 이유."""
    cmd = login("t_cmd4", "COMMANDER")
    pid = run(client, reported, cmd)["result"]["prediction_id"]
    risk = client.get(f"{URL}/{reported['id']}/predictions/{pid}/risk", headers=cmd).json()["data"]
    assert risk["prediction_id"] == pid and risk["method_version"] == "risk-2"
    assert risk["status"] == "PARTIAL" and len(risk["factors"]) == 25
    assert 1 <= risk["score"] <= 5 and risk["unavailable_reasons"]
    by = {f["code"]: f for f in risk["factors"]}
    assert all(by[c]["included"] for c in ("wind_speed", "humidity", "temperature"))
    assert by["slope_up"]["included"] is False and by["slope_up"]["reason"]
    assert by["fire_area"]["included"]
    assert abs((risk["hazard"] * risk["exposure"]) ** 0.5 - risk["score"]) < 0.01
