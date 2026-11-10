"""ELMFIRE worker(구조 B): API ↔ worker 모양(elmfire_wire) · 받는 곳(worker_server) · 부르는 쪽.

worker 서버를 시험 안에서 띄우고(빈 포트) 가짜 예측을 꽂는다 — ELMFIRE 없이 HTTP 그대로 확인한다.
"""

import threading
import time
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from app.predict.base import PredictionRequest, PredictionResult
from app.predict.elmfire_remote import ElmfireWorker
from app.predict.elmfire_wire import pack_request, unpack_request
from app.predict.worker_server import make_server
from app.weather.base import KST, WeatherRequest
from app.weather.elmfire_input import GRID_KEYS, ElmfireWeatherInput, floor_hour
from app.weather.fixed import FixedWeather

BASE_AT = datetime(2025, 3, 22, 12, 50, tzinfo=KST)
SITE = (36.3657, 128.6023)
START = box(1_100_300, 1_819_310, 1_100_600, 1_819_490)


def request(lead=True):
    rng = np.random.default_rng(0)
    grids = {k: rng.random((9, 2, 6), dtype="float32") for k in GRID_KEYS}
    weather = FixedWeather(wind_ms=4.0).fetch(WeatherRequest(SITE, BASE_AT))
    inp = ElmfireWeatherInput(
        floor_hour(BASE_AT),
        grids,
        weather,
        {"source": "stations", "stations": ["278"]},
        {k: rng.random((2, 6), dtype="float32") for k in GRID_KEYS} if lead else None,
    )
    return PredictionRequest(START, BASE_AT, SITE, None, inp)


class FakeElmfire:
    """가짜 예측: 받은 화선에서 시간마다 30 m씩 넓힌다. fail로 오류, delay로 실행 시간."""

    name = "elmfire"

    def __init__(self, fail=None, delay=0.0):
        self.fail, self.delay, self.seen = fail, delay, []
        self.active = self.max_active = 0

    def predict(self, req):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.seen.append(req)
            time.sleep(self.delay)
            if self.fail:
                raise self.fail
            slices = [req.start.buffer(30 * h) for h in range(1, 9)]
            return PredictionResult(slices, req.elmfire_weather.weather, self.name, {"wall_s": 1.5})
        finally:
            self.active -= 1


@pytest.fixture
def worker():
    """(시작할 가짜 예측) → 빈 포트에 띄운 worker 주소."""
    servers = []

    def start(fake):
        server = make_server(("127.0.0.1", 0), fake)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def test_wire_round_trip_keeps_input_and_lead():
    req = request()
    back = unpack_request(pack_request(req))
    a, b = req.elmfire_weather, back.elmfire_weather
    assert back.start.equals(req.start) and back.base_at == req.base_at and back.site == SITE
    assert b.t0 == a.t0 and b.weather == a.weather and b.meta == a.meta
    for k in GRID_KEYS:
        np.testing.assert_array_equal(a.grids[k], b.grids[k])
        np.testing.assert_array_equal(a.lead[k], b.lead[k])
    assert unpack_request(pack_request(request(lead=False))).elmfire_weather.lead is None
    with pytest.raises(ValueError, match="모양이 틀립니다"):
        unpack_request(b"not npz")


def test_worker_runs_and_returns_slices(worker):
    fake = FakeElmfire()
    url = worker(fake)
    req = request()
    result = ElmfireWorker(url).predict(req)
    assert len(result.slices) == 8
    assert result.slices[0].area == pytest.approx(START.buffer(30).area, rel=1e-9)
    assert result.weather is req.elmfire_weather.weather and result.source == "elmfire"
    assert result.meta["wall_s"] == 1.5 and result.meta["worker"] == url
    np.testing.assert_array_equal(  # worker가 받은 격자 = 보낸 격자
        fake.seen[0].elmfire_weather.grids["ws"], req.elmfire_weather.grids["ws"]
    )


def test_worker_errors_become_value_or_runtime_errors(worker):
    with pytest.raises(ValueError, match="덱 범위"):  # 422 → 작업 실패 PREDICTION_INVALID
        ElmfireWorker(worker(FakeElmfire(fail=ValueError("화선이 덱 범위 밖입니다")))).predict(
            request()
        )
    with pytest.raises(RuntimeError, match="504"):  # 시간 초과
        ElmfireWorker(worker(FakeElmfire(fail=TimeoutError("ELMFIRE 300초")))).predict(request())
    with pytest.raises(RuntimeError, match="500"):  # ELMFIRE 오류
        ElmfireWorker(worker(FakeElmfire(fail=RuntimeError("ELMFIRE 실패")))).predict(request())
    with pytest.raises(RuntimeError, match="연결하지 못했습니다"):  # worker가 꺼져 있음
        ElmfireWorker("http://127.0.0.1:9", timeout_s=2).predict(request())
    with pytest.raises(ValueError, match="기상 입력이 없습니다"):
        ElmfireWorker("http://127.0.0.1:9").predict(PredictionRequest(START, BASE_AT, SITE))


def test_worker_runs_one_at_a_time_and_answers_health(worker):
    import json
    import urllib.request

    fake = FakeElmfire(delay=0.3)
    url = worker(fake)
    out = []
    threads = [
        threading.Thread(target=lambda: out.append(ElmfireWorker(url).predict(request())))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    time.sleep(0.1)
    health = json.loads(urllib.request.urlopen(f"{url}/health", timeout=5).read())
    for t in threads:
        t.join()
    assert len(out) == 2 and fake.max_active == 1  # 동시에 두 번 돌지 않는다
    assert health == {"ok": True, "busy": True}  # 실행 중에도 상태는 바로 답한다
    assert max(r.meta["queue_wait_s"] for r in out) >= 0.1  # 뒤 요청은 기다렸다


def test_predictor_from_settings_uses_worker_when_url_given(monkeypatch):
    from app.services import prediction

    settings = SimpleNamespace(
        predictor="elmfire",
        elmfire_worker_url="http://wfg-worker:8100",
        elmfire_worker_timeout_s=600.0,
        predictor_deck="/deck",
    )
    monkeypatch.setattr(prediction, "get_settings", lambda: settings)
    p = prediction.predictor_from_settings()
    assert isinstance(p, ElmfireWorker) and p.url == "http://wfg-worker:8100"
    assert p.timeout_s == 600.0 and p.name == "elmfire"
