"""지금 날씨(로컬 DB): 테이블 먼저, 없으면 기상청 1번 → 저장. 기상청은 가짜."""

from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.db.models import Incident, WeatherHour
from app.db.models.enums import IncidentStatus
from app.predict.synthetic import SyntheticPredictor
from app.services.prediction import run_prediction
from app.services.weather import weather_now
from app.weather import WeatherUnavailable
from app.weather.base import KST

NOW = datetime(2026, 11, 9, 21, 50, tzinfo=KST)  # 최신 관측 정시 = 21:00
VALUES = {"wind_dir": 135.0, "wind_speed": 0.1, "temp": 7.5, "humidity": 100.0, "precip": 0.0}


class FakeObserver:
    def __init__(self, fail: bool = False):
        self.calls: list[datetime] = []
        self.fail = fail

    def observation(self, site, at):
        self.calls.append(at)
        if self.fail:
            raise WeatherUnavailable("기상청 연결 실패(테스트)")
        return dict(VALUES)


def rows(db) -> int:
    return db.scalar(select(func.count()).select_from(WeatherHour))


def test_first_call_fetches_and_saves_then_reuses(db, reported):
    incident = db.get(Incident, int(reported["id"]))
    observer, before = FakeObserver(), rows(db)
    out = weather_now(db, incident, observer=observer, now=NOW)
    assert observer.calls == [datetime(2026, 11, 9, 21, tzinfo=KST)]
    assert out["source"] == "KMA_NCST" and out["wind_dir_name"] == "남동"
    assert out["observed_at"] == datetime(2026, 11, 9, 21, tzinfo=KST)
    assert out["next_refresh_at"] == datetime(2026, 11, 9, 22, 40, tzinfo=KST)
    assert out["stale"] is False and rows(db) == before + 1
    again = weather_now(db, incident, observer=observer, now=NOW.replace(minute=59))
    assert len(observer.calls) == 1  # 같은 정시는 다시 부르지 않음
    assert again["temp"] == 7.5 and rows(db) == before + 1


def test_kma_failure_gives_last_saved_or_unavailable(db, reported):
    incident = db.get(Incident, int(reported["id"]))
    with pytest.raises(WeatherUnavailable):  # 저장된 관측도 없음
        weather_now(db, incident, observer=FakeObserver(fail=True), now=NOW)
    weather_now(db, incident, observer=FakeObserver(), now=NOW)  # 21:00 저장
    out = weather_now(
        db, incident, observer=FakeObserver(fail=True), now=NOW.replace(hour=22, minute=45)
    )
    assert out["stale"] is True and out["next_refresh_at"] is None
    assert out["observed_at"] == datetime(2026, 11, 9, 21, tzinfo=KST)


def test_not_live_or_ended_uses_latest_prediction(db, reported, make_user):
    incident = db.get(Incident, int(reported["id"]))
    with pytest.raises(WeatherUnavailable, match="예측 전"):
        weather_now(db, incident, observer=None, now=NOW)
    user = make_user("t_wx", "COMMANDER")
    run_prediction(
        db, incident_id=incident.id, predictor=SyntheticPredictor(), requested_by=user.id
    )
    out = weather_now(db, incident, observer=None, now=NOW)
    assert out["source"] == "SYNTHETIC" and out["wind_speed"] == 2.0
    assert out["wind_dir_name"] == "서"
    assert out["temp"] is None and out["next_refresh_at"] is None
    incident.status = IncidentStatus.ENDED  # 종료된 산불은 기상청을 부르지 않음
    observer = FakeObserver()
    assert weather_now(db, incident, observer=observer, now=NOW)["source"] == "SYNTHETIC"
    assert observer.calls == []


URL = "/api/v1/incidents/{}/weather/now"


def test_api_weather_now(client, login, reported, monkeypatch):
    cmd, rep = login("t_wx_cmd", "COMMANDER"), login("t_wx_rep", "REPORTER")
    url = URL.format(reported["id"])
    monkeypatch.setattr("app.routers.weather.observer_from_settings", lambda: None)
    r = client.get(url, headers=cmd)  # 실시간 원천 없음 + 예측 전
    assert r.status_code == 503 and r.json()["error"]["code"] == "WEATHER_UNAVAILABLE"
    observer = FakeObserver()
    monkeypatch.setattr("app.routers.weather.observer_from_settings", lambda: observer)
    data = client.get(url, headers=cmd).json()["data"]
    assert data["source"] == "KMA_NCST" and data["wind_dir_name"] == "남동"
    assert data["observed_at"].endswith("+09:00") and data["stale"] is False
    assert client.get(url, headers=cmd).json()["data"] == data and len(observer.calls) == 1
    assert client.get(url, headers=rep).status_code == 403  # 보고자는 못 봄(PRED-04와 같음)
