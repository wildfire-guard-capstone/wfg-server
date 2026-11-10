"""PRED-01 예측 요청 · PRED-05 작업 조회 · PRED-02 예측 상세 · PRED-03 도형 · PRED-04 기상,
RISK-01 위험도(가짜)."""

import json
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Header, Path, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import Db, SessionFactory, require_roles
from app.api.envelope import Envelope, ok
from app.api.errors import ApiError, not_found
from app.db.models import FirePerimeter, Prediction, User
from app.db.models.enums import IncidentStatus
from app.repositories import incidents, predictions
from app.routers.incidents import IncidentId, load_incident
from app.schemas.prediction import OperationOut, PredictionOut, PredictionRequestIn, SliceSummary
from app.schemas.risk import RiskOut
from app.services import operations
from app.services.prediction import fetch_weather, run_prediction_operation
from app.services.risk import compute_risk
from app.services.weather import provider_from_settings
from app.weather import WeatherUnavailable

router = APIRouter(prefix="/incidents/{incident_id}", tags=["예측"])
Commander = require_roles("COMMANDER")
Analyst = require_roles("COMMANDER", "VIEWER")
PredictionId = Annotated[int, Path(gt=0)]


def load_prediction(db: Session, user: User, incident_id: int, prediction_id: int) -> Prediction:
    """관할 산불의 예측만. 다른 산불의 예측 id면 404."""
    load_incident(db, user, incident_id)
    p = db.get(Prediction, prediction_id)
    if p is None or p.incident_id != incident_id:
        raise not_found("예측을 찾을 수 없습니다.")
    return p


@router.post("/predictions", status_code=202, response_model=Envelope[OperationOut])
def request_prediction(
    incident_id: IncidentId,
    body: PredictionRequestIn,
    request: Request,
    background: BackgroundTasks,
    user: Commander,
    db: Db,
    session_factory: SessionFactory,
    idempotency_key: Annotated[str | None, Header()] = None,
):
    """PRED-01. 최신 화선으로만 예측한다. 202 = 시작했다는 뜻, 결과는 PRED-05로 본다.

    기상은 여기서 먼저 받는다(설정 WEATHER의 원천, 9시간). 못 받으면 작업을 시작하지 않고
    503 WEATHER_UNAVAILABLE(명세 v0.4 그대로). 받은 기상은 작업에 넘겨 예측 · 제안에 같이 쓴다.
    """
    incident = load_incident(db, user, incident_id)
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "종료된 산불은 예측할 수 없습니다.")
    if incidents.current_perimeter(db, incident_id).id != body.expected_perimeter_id:
        raise ApiError(409, "STALE_PERIMETER", "화선이 바뀌었습니다. 새로 고친 뒤 다시 하세요.")
    if operations.is_running(incident_id):  # 이미 계산 중이면 기상청을 부르지 않는다
        raise ApiError(409, "PREDICTION_RUNNING", "이 산불의 예측을 이미 계산하는 중입니다.")
    try:
        weather = fetch_weather(db, incident_id, provider_from_settings())
    except (
        WeatherUnavailable,
        ValueError,
    ) as exc:  # 원천 실패 · 설정 없음 · 모양 오류(키 없는 문장)
        raise ApiError(503, "WEATHER_UNAVAILABLE", f"기상을 받지 못했습니다: {exc}") from None
    op = operations.start("PREDICTION", incident_id, user.id)
    if op is None:
        raise ApiError(409, "PREDICTION_RUNNING", "이 산불의 예측을 이미 계산하는 중입니다.")
    background.add_task(
        run_prediction_operation, op, body.expected_perimeter_id, session_factory, weather
    )
    return ok(request, OperationOut.of(op))


@router.get("/operations/{operation_id}", response_model=Envelope[OperationOut])
def get_operation(
    incident_id: IncidentId, operation_id: str, request: Request, user: Commander, db: Db
):
    """PRED-05. 요청한 사람 · 그 산불의 작업만. 실패도 200 안에서 status=FAILED로 알린다."""
    load_incident(db, user, incident_id)
    op = operations.get(operation_id)
    if op is None or op.incident_id != incident_id or op.user_id != user.id:
        raise not_found("작업을 찾을 수 없습니다(서버를 다시 켜면 작업 기록이 사라집니다).")
    return ok(request, OperationOut.of(op))


@router.get("/predictions/{prediction_id}", response_model=Envelope[PredictionOut])
def get_prediction(
    incident_id: IncidentId, prediction_id: PredictionId, request: Request, user: Analyst, db: Db
):
    """PRED-02. 성공한 예측만 있다(진행 · 실패는 작업 조회)."""
    p = load_prediction(db, user, incident_id, prediction_id)
    version = db.get(FirePerimeter, p.perimeter_id).version_no
    slices = [
        SliceSummary(hour=h, at=p.base_at + timedelta(hours=h), area_ha=round(area, 2))
        for h, area, _ in predictions.slice_rows(db, prediction_id)
    ]
    out = PredictionOut(
        id=p.id,
        incident_id=p.incident_id,
        perimeter_id=p.perimeter_id,
        perimeter_version=version,
        base_at=p.base_at,
        requested_by=p.requested_by,
        created_at=p.created_at,
        slices=slices,
    )
    return ok(request, out)


@router.get("/predictions/{prediction_id}/slices", response_model=Envelope[dict[str, Any]])
def get_slices(
    incident_id: IncidentId,
    prediction_id: PredictionId,
    request: Request,
    user: Analyst,
    db: Db,
    hour: int | None = Query(None, ge=1, le=8),
):
    """PRED-03. P1~P8(또는 hour 하나) FeatureCollection. P(n)은 P(n-1)을 품는다."""
    p = load_prediction(db, user, incident_id, prediction_id)
    features = [
        {
            "type": "Feature",
            "id": f"{p.id}-{h}",
            "geometry": json.loads(geojson),
            "properties": {
                "prediction_id": str(p.id),
                "hour": h,
                "at": SliceSummary(hour=h, at=p.base_at + timedelta(hours=h), area_ha=0).model_dump(
                    mode="json"
                )["at"],
                "area_ha": round(area, 2),
            },
        }
        for h, area, geojson in predictions.slice_rows(db, prediction_id, hour)
    ]
    return ok(request, {"type": "FeatureCollection", "features": features})


@router.get("/predictions/{prediction_id}/weather", response_model=Envelope[dict[str, Any]])
def get_weather(
    incident_id: IncidentId, prediction_id: PredictionId, request: Request, user: Analyst, db: Db
):
    """PRED-04. 그 예측에 넣은 기상 그대로(새로 받지 않음).

    가짜 · 파일 예측은 기온 · 습도 · 위치가 없어 명세 Weather와 다를 수 있다(04 수정 필요 5번).
    """
    p = load_prediction(db, user, incident_id, prediction_id)
    return ok(request, p.weather)


@router.get("/predictions/{prediction_id}/risk", response_model=Envelope[RiskOut])
def get_risk(
    incident_id: IncidentId, prediction_id: PredictionId, request: Request, user: Analyst, db: Db
):
    """RISK-01. 계산은 services.risk(risk/ 패키지) — 못 구한 변수는 빼고 PARTIAL."""
    p = load_prediction(db, user, incident_id, prediction_id)
    return ok(request, compute_risk(db, p))
