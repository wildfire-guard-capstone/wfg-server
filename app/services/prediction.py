"""예측 돌리기: 현재 화선에서 예측 제공자를 불러 P1 ~ P8을 저장한다."""

from geoalchemy2.shape import to_shape
from sqlalchemy.orm import Session

from app.predict import PredictionRequest, Predictor
from app.repositories import incidents, predictions
from app.repositories.engine_inputs import KST, site_of


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
