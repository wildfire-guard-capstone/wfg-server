"""예측 저장 · 읽기 — predictions 1행 + prediction_slices 8행(DB 설계 2.3)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import EventLog, Prediction, PredictionSlice
from app.db.models._types import SRID
from app.predict.base import HOURS, PredictionResult

# 앞 시간 범위가 다음 범위 밖으로 삐져나와도 되는 몫(래스터 → 폴리곤 경계 오차)
NEST_TOLERANCE = 0.001
# 시작이 점(발화점)일 때 P1 경계와 떨어져도 되는 거리(m). ELMFIRE 면은 칸(30 m) 경계를 10 m까지
# 다듬어서(spread.perimeter_run.polygonize) 1 m로는 빡빡하다(11/11 EC2 시험)
POINT_TOLERANCE_M = 10.0


@dataclass(frozen=True)
class StoredPrediction:
    id: int
    incident_id: int
    perimeter_id: int
    base_at: datetime
    weather: dict[str, Any]
    slices: list[BaseGeometry]  # P1 ~ P8


def to_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    geom = make_valid(geom)
    polys = [g for g in getattr(geom, "geoms", [geom]) if isinstance(g, Polygon | MultiPolygon)]
    parts = [p for g in polys for p in getattr(g, "geoms", [g])]
    if not parts:
        raise ValueError("면이 아닌 범위입니다")
    return MultiPolygon(parts)


def check_slices(start: BaseGeometry, slices: list[BaseGeometry]) -> list[MultiPolygon]:
    """P1 ~ P8 검사: 8개 · 면 · 비어 있지 않음 · P0 ⊆ P1 ⊆ … ⊆ P8. 통과하면 MultiPolygon 목록."""
    if len(slices) != HOURS:
        raise ValueError(f"범위가 {len(slices)}개입니다(P1 ~ P{HOURS}, {HOURS}개 필요)")
    out = [to_multipolygon(g) for g in slices]
    for i, g in enumerate(out, start=1):
        if g.is_empty:
            raise ValueError(f"P{i}가 비어 있습니다")
    prev: BaseGeometry = start
    for i, cur in enumerate(out, start=1):
        if prev.area == 0:  # 시작이 점(발화점)이면 덮는지만 본다(다듬기 폭만큼 봐준다)
            ok = cur.buffer(POINT_TOLERANCE_M).covers(prev)
        else:
            ok = prev.difference(cur).area <= prev.area * NEST_TOLERANCE
        if not ok:
            raise ValueError(f"P{i}가 앞 범위(P{i - 1})를 덮지 않습니다(누적 범위여야 함)")
        prev = cur
    return out


def save_prediction(
    session: Session,
    *,
    incident_id: int,
    perimeter_id: int,
    start: BaseGeometry,
    base_at: datetime,
    result: PredictionResult,
    requested_by: int,
) -> int:
    """검사한 뒤 예측 1행 + 범위 8행을 넣고 예측 id를 돌려준다. 커밋은 부르는 쪽이 한다."""
    slices = check_slices(start, result.slices)
    prediction = Prediction(
        incident_id=incident_id,
        perimeter_id=perimeter_id,
        base_at=base_at,
        weather=result.weather,
        requested_by=requested_by,
    )
    session.add(prediction)
    session.flush()
    session.add_all(
        PredictionSlice(prediction_id=prediction.id, hour=h, geom=from_shape(g, srid=SRID))
        for h, g in enumerate(slices, start=1)
    )
    session.add(
        EventLog(
            user_id=requested_by,
            incident_id=incident_id,
            action="PREDICTION_CREATED",
            details={"prediction_id": prediction.id, "predictor": result.source, **result.meta},
        )
    )
    session.flush()
    return prediction.id


def get_prediction(session: Session, prediction_id: int) -> StoredPrediction:
    p = session.get(Prediction, prediction_id)
    if p is None:
        raise ValueError(f"예측 {prediction_id}이 없습니다")
    rows = session.scalars(
        select(PredictionSlice)
        .where(PredictionSlice.prediction_id == prediction_id)
        .order_by(PredictionSlice.hour)
    ).all()
    return StoredPrediction(
        p.id, p.incident_id, p.perimeter_id, p.base_at, p.weather, [to_shape(r.geom) for r in rows]
    )


def latest_prediction_id(session: Session, incident_id: int) -> int | None:
    """현재 예측 = 그 산불에서 id가 가장 큰 행(DB 설계 2.3)."""
    return session.scalar(
        select(Prediction.id)
        .where(Prediction.incident_id == incident_id)
        .order_by(Prediction.id.desc())
        .limit(1)
    )


def slice_rows(session: Session, prediction_id: int, hour: int | None = None) -> list[tuple]:
    """(시간, 면적 ha, 4326 GeoJSON 문자열) — 시간 순. hour를 주면 그 시간만."""
    q = (
        "SELECT hour, ST_Area(geom) / 10000, ST_AsGeoJSON(ST_Transform(geom, 4326), 6)"
        " FROM prediction_slices WHERE prediction_id = :p"
    )
    params: dict = {"p": prediction_id}
    if hour is not None:
        q += " AND hour = :h"
        params["h"] = hour
    return list(session.execute(text(q + " ORDER BY hour"), params))
