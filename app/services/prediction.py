"""예측 돌리기: 현재 화선에서 예측 제공자를 불러 P1 ~ P8을 저장한다."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from geoalchemy2.shape import to_shape
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.config import get_settings
from app.db.models import Incident
from app.db.models.enums import IncidentStatus
from app.predict import PredictionRequest, Predictor, get_predictor
from app.repositories import event_logs, incidents, predictions
from app.repositories.engine_inputs import KST, site_of
from app.services import operations

log = logging.getLogger(__name__)
SessionFactory = Callable[[], AbstractContextManager[Session]]


def run_prediction(
    session: Session, *, incident_id: int, predictor: Predictor, requested_by: int
) -> int:
    """예측 id를 돌려준다. 커밋은 부르는 쪽이 한다.

    1. 현재 화선(없으면 v0 발화점) = P0, 그 관측 시각 = 0시
    2. 예측 제공자에 넘긴다 — 어떤 예측이든 같은 약속(app.predict.base)
    3. 검사(8개 · 면 · 누적) 후 저장
    """
    incident = incidents.get_incident(session, incident_id)
    perimeter = incidents.current_perimeter(session, incident_id)  # 1
    start = to_shape(perimeter.geom)
    base_at = perimeter.observed_at.astimezone(KST)
    request = PredictionRequest(start, base_at, site_of(to_shape(incident.ignition_point)))
    result = predictor.predict(request)  # 2
    return predictions.save_prediction(  # 3
        session,
        incident_id=incident_id,
        perimeter_id=perimeter.id,
        start=start,
        base_at=base_at,
        result=result,
        requested_by=requested_by,
    )


def predictor_from_settings() -> Predictor:
    """설정(PREDICTOR · PREDICTOR_PATH …)으로 예측 제공자를 고른다 — 팀원이 갈아 끼우는 자리."""
    s = get_settings()
    options = {}
    if s.predictor == "file":
        options = {"path": s.predictor_path, "crs": s.predictor_crs}
        if s.predictor_weather:
            options["weather_path"] = s.predictor_weather
    return get_predictor(s.predictor, **options)


def _check_still_current(db: Session, incident_id: int, perimeter_id: int) -> None:
    """저장 직전 재검사: 그사이 종료되거나 화선이 바뀌었으면 옛 결과를 최신으로 저장하지 않는다."""
    incident = db.get(Incident, incident_id)
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "계산하는 동안 산불이 종료되었습니다.")
    if incidents.current_perimeter(db, incident_id).id != perimeter_id:
        raise ApiError(409, "STALE_PERIMETER", "계산하는 동안 화선이 바뀌었습니다.")


def run_prediction_operation(
    op: operations.Operation, perimeter_id: int, session_factory: SessionFactory
) -> None:
    """PRED-01 작업: 예측(SPREAD) → 저장 직전 재검사 → 커밋(COMMIT).

    실패하면 예측을 남기지 않고(직전 예측이 그대로 현재 예측) PREDICTION_FAILED만 기록한다.

    규칙 판정 · 근거 · 제안 생성(RULES · EVIDENCE · PROPOSAL_GENERATION)은 LLM 응답 형식이
    정해진 뒤 이 자리에 붙인다. 그래서 지금 result.proposal_id는 늘 null이다.
    """
    operations.update(op, status="RUNNING", stage="SPREAD")
    with session_factory() as db:
        try:
            pid = run_prediction(
                db,
                incident_id=op.incident_id,
                predictor=predictor_from_settings(),
                requested_by=op.user_id,
            )
            operations.update(op, stage="COMMIT")
            _check_still_current(db, op.incident_id, perimeter_id)
            db.commit()
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 작업 실패로 알린다
            db.rollback()
            if isinstance(exc, ApiError):
                error = {"code": exc.code, "message": exc.message}
            elif isinstance(exc, ValueError):
                error = {"code": "PREDICTION_INVALID", "message": str(exc)}
            else:
                log.exception("예측 작업 실패 (%s)", op.id)
                error = {"code": "INTERNAL_ERROR", "message": "예측 중 오류가 발생했습니다."}
            event_logs.add(
                db,
                "PREDICTION_FAILED",
                user_id=op.user_id,
                incident_id=op.incident_id,
                details={"operation_id": op.id, **error},
            )
            db.commit()
            operations.update(op, status="FAILED", error=error)
            return
    operations.update(
        op, status="SUCCEEDED", result={"prediction_id": str(pid), "proposal_id": None}
    )
