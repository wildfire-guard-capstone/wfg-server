"""규칙 엔진 입력 모으기: 산불 · 화선 · 예측 · 바탕 데이터(DB)
→ rules.context.Inputs + 산불 정보(L-02).

규칙 엔진은 DB를 모른다. DB 값을 엔진 자료형으로 바꾸는 일은 여기서만 한다.
시각: DB는 시간대가 있는 값(timestamptz), 엔진은 한국 시간 그대로(시간대 없음)를 쓴다.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from geoalchemy2.shape import to_shape
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from app.core.time import KST
from app.db.models import Incident
from app.repositories import base_data, incidents, predictions
from rules.context import Inputs
from rules.core.places import Deployed
from rules.core.stage import Stage
from rules.core.villages import VillageField

AREA_BUFFER_M = 20_000  # P8 둘레 20 km 안 바탕 데이터를 읽는다(도로 · 대피소 · 시군구 경계 …)

# incidents.situation.deployed 키 → 엔진 라벨(rules.bundles.resources.LABELS, 산림청 상황 보고 양식)
DEPLOYED_LABELS = {
    "helicopter": {
        "forest": "산림청",
        "local": "지자체",
        "fire": "소방",
        "military": "군",
        "police": "경찰",
    },
    "crew": {
        "special_crew": "진화대",
        "forest_officials": "공무원",
        "fire": "소방",
        "military": "군",
        "police": "경찰",
        "other": "기타",
    },
    "vehicle": {"forest_vehicle": "지휘·진화차", "fire_truck": "소방차", "other": "기타"},
}
VILLAGE_FIELDS = ("evac_order", "evacuated", "pre_evac_done", "alert_at", "reported_at")
_TO_WGS84 = Transformer.from_crs(5179, 4326, always_xy=True)


def kst(t: datetime) -> datetime:
    """DB 시각 → 엔진 시각(한국 시간, 시간대 없음). 시간대가 없는 값은 이미 한국 시간으로 본다."""
    return t.astimezone(KST).replace(tzinfo=None) if t.tzinfo else t


def site_of(point: BaseGeometry) -> tuple[float, float]:
    """EPSG:5179 점 → (위도, 경도)."""
    lon, lat = _TO_WGS84.transform(point.x, point.y)
    return lat, lon


def hourly_wind(weather: Mapping[str, Any]) -> tuple[dict[int, float], dict[int, float]]:
    """predictions.weather.hours(0시부터 9개) → 0시 뒤 몇 시간 → 풍속 · 풍향. 빈 값은 뺀다."""
    speed, direction = {}, {}
    for i, h in enumerate(weather.get("hours", [])):
        if h.get("wind_speed") is not None:
            speed[i] = float(h["wind_speed"])
        if h.get("wind_dir") is not None:
            direction[i] = float(h["wind_dir"])
    return speed, direction


def village_fields(situation: Mapping[str, Any]) -> dict[int, VillageField]:
    """situation.villages(키 = villages.id 문자열) → 마을 id → 현장 입력."""
    return {
        int(vid): VillageField(**{k: v for k, v in values.items() if k in VILLAGE_FIELDS})
        for vid, values in situation.get("villages", {}).items()
    }


def deployed_of(situation: Mapping[str, Any]) -> Deployed | None:
    """situation.deployed → 엔진 투입 수(라벨). 보고 전이면 None. `total`은 엔진이 다시 더한다."""
    d = situation.get("deployed")
    if d is None:
        return None
    out = {}
    for group, labels in DEPLOYED_LABELS.items():
        values = {k: v for k, v in d.get(group, {}).items() if k != "total"}
        unknown = set(values) - set(labels)
        if unknown:
            raise ValueError(f"situation.deployed.{group}에 모르는 키: {sorted(unknown)}")
        out[group] = {labels[k]: int(v) for k, v in values.items()}
    return Deployed(**out, reported_at=d.get("reported_at"))


def prev_alerts_of(context: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    """직전 제안서의 엔진 결과(proposals.context.alerts) → (시군구, 읍면, go · set)."""
    areas = context.get("alerts", {}).get("areas", [])
    return tuple((a["sigungu"], a["emd"], a["level"]) for a in areas)


def incident_info(session: Session, incident: Incident) -> dict[str, Any]:
    """L-02 산불 기본 정보 — 모든 LLM 호출의 `common.incident`."""
    sigungu, emd = incidents.incident_names(session, incident)
    started = kst(incident.started_at) if incident.started_at else None
    reported = kst(incident.reported_at)
    return {
        "name": incidents.incident_name(session, incident),
        "sigungu": sigungu,
        "emd": emd,
        "address": incident.address,
        "started_at": f"{started:%Y-%m-%d %H:%M}" if started else None,
        "reported_at": f"{reported:%Y-%m-%d %H:%M}",
        "status": incident.status.value,
    }


def build_inputs(
    session: Session,
    *,
    incident_id: int,
    reader: str,
    prediction_id: int | None = None,
    generated_at: datetime | None = None,
) -> tuple[Inputs, dict[str, Any]]:
    """예측 하나(없으면 그 산불의 최신 예측)로 규칙 엔진 입력과 산불 정보를 만든다.

    1. 산불 · 예측 · 예측의 시작 화선(P0)을 읽는다 → P0 ~ P8
    2. 범위 = P8 둘레 20 km. 그 안 바탕 데이터를 읽는다(주택은 P8 안만 — P5 동수만 셈)
    3. 기상 = 예측에 넣은 시간별 기상(0시 기온 · 습도 포함)
    4. 현장 입력 = incidents.situation, 직전 단계 · 문자 단계 = 직전 제안서
    """
    incident = incidents.get_incident(session, incident_id)  # 1
    pid = prediction_id or predictions.latest_prediction_id(session, incident_id)
    if pid is None:
        raise ValueError(f"산불 {incident_id}에 예측이 없습니다(예측부터 만들어야 함)")
    pred = predictions.get_prediction(session, pid)
    if pred.incident_id != incident_id:
        raise ValueError(f"예측 {pid}은 산불 {incident_id}의 예측이 아닙니다")
    ranges = [incidents.perimeter_shape(session, pred.perimeter_id), *pred.slices]
    area = ranges[-1].buffer(AREA_BUFFER_M)  # 2
    sido, sigungu = base_data.regions(session, area)
    places = base_data.facilities(session, area)
    features = base_data.map_features(session, area, house_area=ranges[-1])
    wind_ms, wind_deg = hourly_wind(pred.weather)  # 3
    first = (pred.weather.get("hours") or [{}])[0]
    prev = incidents.latest_proposal(session, incident_id)  # 4
    ignition = to_shape(incident.ignition_point)
    inputs = Inputs(
        base_at=kst(pred.base_at),
        generated_at=kst(generated_at or datetime.now(KST)),
        reader=reader,
        prev_stage=Stage[prev.judged_stage.name] if prev else None,
        ranges=ranges,
        ignition=ignition,
        wind_ms=wind_ms,
        wind_deg=wind_deg,
        temp_c=first.get("temp"),
        rh_pct=first.get("humidity"),
        site=site_of(ignition),
        sido=sido,
        sigungu=sigungu,
        neighbors=base_data.neighbors(session, area),
        villages=base_data.villages(session, area),
        field=village_fields(incident.situation),
        deployed=deployed_of(incident.situation),
        bases=base_data.bases(session, incident_id),
        prev_alerts=prev_alerts_of(prev.context) if prev else (),
        **places,
        **features,
    )
    return inputs, incident_info(session, incident)
