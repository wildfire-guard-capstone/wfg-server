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


def proposal_op(client, inc, headers, op):
    """예측 작업이 넘겨준 제안서 작업."""
    op_id = op["result"]["proposal_operation_id"]
    return client.get(f"{URL}/{inc['id']}/operations/{op_id}", headers=headers).json()["data"]


def test_predict_then_read_prediction_slices_weather(client, login, reported):
    cmd, view = login("t_cmd", "COMMANDER"), login("t_view", "VIEWER")
    op = run(client, reported, cmd)
    assert op["status"] == "SUCCEEDED" and op["result"]["proposal_operation_id"]
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
    """예측 작업이 끝나면 제안서 작업으로 넘어가 저장까지. 산불 대응단계 = 엔진 판정."""
    cmd = login("t_cmd5", "COMMANDER")
    op = run(client, reported, cmd)
    assert op["status"] == "SUCCEEDED", op
    prop = proposal_op(client, reported, cmd, op)
    assert prop["status"] == "SUCCEEDED" and prop["kind"] == "PROPOSAL_REGENERATION", prop
    assert prop["result"]["correction_id"] is None
    pid, proposal_id = int(op["result"]["prediction_id"]), int(prop["result"]["proposal_id"])
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


class BrokenWriter:
    name = "broken"

    def write(self, llm_input):
        raise RuntimeError("GPU 서버에 연결할 수 없음")


def count(db, sql, inc):
    return db.scalar(text(sql), {"i": int(inc["id"])})


def test_failed_proposal_keeps_prediction_and_stage(client, login, reported, db, monkeypatch):
    """제안서 생성이 실패해도 예측 · 대응단계는 남고, PROP-05로 다시 만든다."""
    cmd = login("t_cmd6", "COMMANDER")
    with monkeypatch.context() as m:
        m.setattr("app.services.prediction.writer_from_settings", lambda: BrokenWriter())
        op = run(client, reported, cmd)
    assert op["status"] == "SUCCEEDED", op
    prop = proposal_op(client, reported, cmd, op)
    assert prop["status"] == "FAILED" and prop["error"]["code"] == "PROPOSAL_FAILED"
    pid = op["result"]["prediction_id"]
    assert count(db, "SELECT count(*) FROM predictions WHERE incident_id = :i", reported) == 1
    assert count(db, "SELECT count(*) FROM proposals WHERE incident_id = :i", reported) == 0
    failed = "SELECT count(*) FROM event_logs WHERE incident_id = :i AND action = 'PROPOSAL_FAILED'"
    assert count(db, failed, reported) == 1
    detail = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
    assert detail["latest_prediction_id"] == pid and detail["latest_proposal_id"] is None
    assert detail["running_operation_id"] is None

    url = f"{URL}/{reported['id']}/proposal-regenerations"
    body = {"expected_prediction_id": pid, "situation_revision": detail["situation_revision"]}
    r = client.post(url, json=body, headers=cmd)
    assert r.status_code == 202, r.text
    again = r.json()["data"]["id"]
    done = client.get(f"{URL}/{reported['id']}/operations/{again}", headers=cmd).json()["data"]
    assert done["status"] == "SUCCEEDED", done
    row = db.execute(
        text("SELECT trigger::text, correction_id, seq, judged_stage FROM proposals WHERE id = :p"),
        {"p": int(done["result"]["proposal_id"])},
    ).one()
    assert (row.trigger, row.correction_id, row.seq) == ("PREDICTION", None, 1)
    assert detail["current_stage"] == row.judged_stage  # 단계는 예측 때 이미 정해졌다
    assert client.post(url, json=body, headers=cmd).json()["error"]["code"] == (
        "REGENERATION_NOT_NEEDED"
    )


def test_regeneration_needs_reprediction_after_new_perimeter(
    client, login, reported, db, monkeypatch
):
    """제안서가 없는데 새 화선이 왔으면 다시 만들지 않고 예측부터."""
    cmd = login("t_cmd9", "COMMANDER")
    with monkeypatch.context() as m:
        m.setattr("app.services.prediction.writer_from_settings", lambda: BrokenWriter())
        op = run(client, reported, cmd)
    add_perimeter(db, reported)
    detail = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
    assert detail["needs_reprediction"] is True
    r = client.post(
        f"{URL}/{reported['id']}/proposal-regenerations",
        json={
            "expected_prediction_id": op["result"]["prediction_id"],
            "situation_revision": detail["situation_revision"],
        },
        headers=cmd,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_PERIMETER"


def test_correction_does_not_write_a_proposal_for_an_outdated_prediction(
    client, login, reported, db, monkeypatch
):
    """제안서가 없고 새 화선이 온 뒤의 정정 → 옛 예측의 제안서를 만들지 않는다(예측부터)."""
    from sqlalchemy import select

    from app.db.models import User
    from app.services.assistant import start_regeneration

    cmd = login("t_cmd13", "COMMANDER")
    with monkeypatch.context() as m:
        m.setattr("app.services.prediction.writer_from_settings", lambda: BrokenWriter())
        run(client, reported, cmd)
    user = db.scalar(select(User).where(User.login_id == "t_cmd13"))
    add_perimeter(db, reported)
    assert start_regeneration(db, user, int(reported["id"])) == (None, None, None)
    assert not operations.is_running(int(reported["id"]))


def add_perimeter(db, inc):
    """다음 버전 화선(발화점 둘레 100 m)을 바로 넣는다."""
    db.execute(
        text(
            "INSERT INTO fire_perimeters (incident_id, version_no, geom, observed_at, reported_by)"
            " SELECT i.id, 1, ST_Multi(ST_Buffer(i.ignition_point, 100)),"
            " i.reported_at + interval '1 hour', i.reported_by FROM incidents i WHERE i.id = :i"
        ),
        {"i": int(inc["id"])},
    )
    db.commit()


def test_prediction_is_visible_while_proposal_runs(client, login, reported, db, monkeypatch):
    """제안서를 쓰는 동안 예측 · 단계는 이미 저장돼 있다. 그사이 새 화선이 와도 제안서는 남는다."""
    from app.propose import get_writer
    from app.services import operations as ops

    seen = {}

    class Watching:
        name = "fake"

        def write(self, llm_input):
            detail = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
            running = ops.running(int(reported["id"]))
            seen.update(detail=detail, kind=running.kind, stage=running.stage)
            add_perimeter(db, reported)
            return get_writer("fake").write(llm_input)

    cmd = login("t_cmd10", "COMMANDER")
    monkeypatch.setattr("app.services.prediction.writer_from_settings", lambda: Watching())
    op = run(client, reported, cmd)
    assert (seen["kind"], seen["stage"]) == ("PROPOSAL_REGENERATION", "PROPOSAL_GENERATION")
    assert seen["detail"]["latest_prediction_id"] == op["result"]["prediction_id"]
    assert seen["detail"]["latest_proposal_id"] is None
    assert seen["detail"]["running_operation_id"] == op["result"]["proposal_operation_id"]
    prop = proposal_op(client, reported, cmd, op)
    assert prop["status"] == "SUCCEEDED", prop
    detail = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
    assert detail["latest_proposal_id"] == prop["result"]["proposal_id"]
    assert detail["needs_reprediction"] is True  # 화면은 「재예측 필요」


def test_correction_after_judging_reruns_engine(client, login, reported, db, monkeypatch):
    """예측 때 엔진이 본 뒤 들어온 정정은 제안서 작업이 엔진을 다시 돌려 반영한다."""
    import app.services.prediction as svc

    calls, seen = [], iter([None, 7])  # 판정 때는 정정 없음 → 제안서를 시작할 때는 정정 7
    real = svc.run_engine
    monkeypatch.setattr(svc, "_correction_id", lambda *a: next(seen))
    monkeypatch.setattr(svc, "run_engine", lambda *a, **k: calls.append(1) or real(*a, **k))
    cmd = login("t_cmd12", "COMMANDER")
    op = run(client, reported, cmd)
    assert proposal_op(client, reported, cmd, op)["status"] == "SUCCEEDED"
    assert len(calls) == 2


def test_engine_failure_keeps_prediction(client, login, reported, db, monkeypatch):
    """규칙 엔진이 실패해도 예측은 저장되고, 제안서 작업이 실패로 알린다."""

    def broken(*args, **kwargs):
        raise RuntimeError("엔진 오류")

    monkeypatch.setattr("app.services.prediction.run_engine", broken)
    cmd = login("t_cmd11", "COMMANDER")
    op = run(client, reported, cmd)
    assert op["status"] == "SUCCEEDED", op
    assert proposal_op(client, reported, cmd, op)["error"]["code"] == "PROPOSAL_FAILED"
    detail = client.get(f"{URL}/{reported['id']}", headers=cmd).json()["data"]
    assert detail["latest_prediction_id"] and detail["current_stage"] == "INITIAL"


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
