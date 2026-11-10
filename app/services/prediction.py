"""예측 돌리기: 현재 화선에서 기상을 받고 예측 제공자를 불러 P1 ~ P8을 저장하고,
그 예측으로 제안서를 만든다."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from geoalchemy2.shape import to_shape
from sqlalchemy.orm import Session

from agent.prompt import head_of
from app.api.errors import ApiError
from app.core.config import get_settings
from app.db.models import Incident, Prediction
from app.db.models.enums import IncidentStatus
from app.predict import PredictionRequest, Predictor, get_predictor
from app.propose import ProposalWriter, get_writer
from app.repositories import event_logs, incidents, predictions
from app.repositories.engine_inputs import KST, site_of
from app.repositories.proposals import save_proposal
from app.services import operations
from app.services.engine import run_engine
from app.services.risk import compute_risk
from app.weather import Weather, WeatherProvider, WeatherRequest
from app.weather.base import check_weather

log = logging.getLogger(__name__)
PROPOSAL_STAGES = ("RULES", "PROPOSAL_GENERATION")
SessionFactory = Callable[[], AbstractContextManager[Session]]


def fetch_weather(session: Session, incident_id: int, provider: WeatherProvider) -> Weather:
    """현재 화선 기준 9시간 기상(0시 = 화선 관측 시각을 내림한 정시, 기준점 = 발화점).
    받지 못하면 WeatherUnavailable, 모양이 틀리면 ValueError."""
    incident = incidents.get_incident(session, incident_id)
    perimeter = incidents.current_perimeter(session, incident_id)
    request = WeatherRequest(
        site_of(to_shape(incident.ignition_point)), perimeter.observed_at.astimezone(KST)
    )
    return check_weather(provider.fetch(request))


def run_prediction(
    session: Session,
    *,
    incident_id: int,
    predictor: Predictor,
    requested_by: int,
    weather: Weather | None = None,
) -> int:
    """예측 id를 돌려준다. 커밋은 부르는 쪽이 한다.

    1. 현재 화선(없으면 v0 발화점) = P0, 그 관측 시각 = 0시
    2. 예측 제공자에 넘긴다 — 어떤 예측이든 같은 약속(app.predict.base). 받은 기상(weather)이
       있으면 같이 넘겨 그 기상으로 계산한다(없으면 예측 제공자의 고정값 — 시험 · 개발 명령)
    3. 검사(8개 · 면 · 누적) 후 저장. 기상은 예측에 실제로 쓴 값(결과의 weather) →
       규칙 엔진 · 제안서가 예측과 같은 기상을 쓴다
    """
    incident = incidents.get_incident(session, incident_id)
    perimeter = incidents.current_perimeter(session, incident_id)  # 1
    start = to_shape(perimeter.geom)
    base_at = perimeter.observed_at.astimezone(KST)
    site = site_of(to_shape(incident.ignition_point))
    result = predictor.predict(PredictionRequest(start, base_at, site, weather))  # 2
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
    elif s.predictor == "elmfire":
        if not s.predictor_deck:
            raise ValueError("PREDICTOR=elmfire에는 PREDICTOR_DECK(덱 폴더)이 필요합니다")
        options = {"deck": s.predictor_deck, "elmfire": s.elmfire_bin}
        if s.predictor_work_dir:
            options["work_dir"] = s.predictor_work_dir
        if s.predictor_weather_field == "stations":
            from app.weather.field import station_field  # spread가 있는 곳(GPU 서버)에서만

            if not (s.kma_api_key and s.data_go_kr_api_key):
                raise ValueError(
                    "PREDICTOR_WEATHER_FIELD=stations에는 KMA_API_KEY · "
                    "DATA_GO_KR_API_KEY가 필요합니다"
                )
            options["field_provider"] = station_field(s.kma_api_key, s.data_go_kr_api_key)
        elif s.predictor_weather_field != "point":
            raise ValueError(f"모르는 PREDICTOR_WEATHER_FIELD: {s.predictor_weather_field}")
    return get_predictor(s.predictor, **options)


def writer_from_settings() -> ProposalWriter:
    """설정(PROPOSAL_WRITER)으로 제안 작성기를 고른다 — 가짜 ↔ 팀원 에이전트를 갈아 끼우는 자리."""
    return get_writer(get_settings().proposal_writer)


def reader_of(db: Session, incident_id: int) -> str:
    """제안서를 읽는 사람 = 산불이 난 시군구의 단체장(의성군 → 의성군수).
    계정에는 직함 칸이 없다."""
    sigungu, _ = incidents.incident_names(db, incidents.get_incident(db, incident_id))
    return head_of(sigungu)


def _check_still_current(db: Session, incident_id: int, perimeter_id: int) -> None:
    """저장 직전 재검사: 그사이 종료되거나 화선이 바뀌었으면 옛 결과를 최신으로 저장하지 않는다."""
    incident = db.get(Incident, incident_id)
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "계산하는 동안 산불이 종료되었습니다.")
    if incidents.current_perimeter(db, incident_id).id != perimeter_id:
        raise ApiError(409, "STALE_PERIMETER", "계산하는 동안 화선이 바뀌었습니다.")


def _risk_check(db: Session, prediction_id: int) -> None:
    """위험도 단계. 지금은 저장하지 않고(조회 때 계산, Q-01) 계산되는지만 본다.
    위험도는 제안의 입력이 아니라 실패해도 예측은 성공으로 둔다(04 수정 필요 6번)."""
    try:
        compute_risk(db, db.get(Prediction, prediction_id))
    except Exception:  # noqa: BLE001
        log.exception("위험도 계산 실패(예측은 계속) — 예측 %s", prediction_id)


def _error_of(exc: Exception, op: operations.Operation) -> dict[str, str]:
    """작업 실패 → 작업의 error. 어느 단계에서 났는지로 나눈다."""
    if isinstance(exc, ApiError):
        return {"code": exc.code, "message": exc.message}
    if op.stage == "SPREAD" and isinstance(exc, ValueError):  # 예측 결과 검사(8개 · 면 · 누적)
        return {"code": "PREDICTION_INVALID", "message": str(exc)}
    log.exception("예측 작업 실패 (%s, %s 단계)", op.id, op.stage)
    if op.stage in PROPOSAL_STAGES:
        return {"code": "PROPOSAL_FAILED", "message": "제안서를 만드는 중 오류가 발생했습니다."}
    return {"code": "INTERNAL_ERROR", "message": "예측 중 오류가 발생했습니다."}


def run_prediction_operation(
    op: operations.Operation,
    perimeter_id: int,
    session_factory: SessionFactory,
    weather: Weather | None = None,
) -> None:
    """PRED-01 작업: 예측(SPREAD) → 위험도(RISK) → 규칙 엔진(RULES) → 제안 작성(PROPOSAL_GENERATION)
    → 저장 직전 재검사 · 제안서 저장 · 커밋(COMMIT). 기상(weather)은 예측 요청 때 받아 넘긴다
    (못 받으면 요청이 503 WEATHER_UNAVAILABLE — 명세 v0.4 그대로, 작업은 시작하지 않음).

    예측과 제안서를 한 트랜잭션으로 저장한다. 어디서든 실패하면 둘 다 남기지 않고(직전 예측 ·
    제안서가 그대로 최신) PREDICTION_FAILED만 기록한다.
    근거 검색은 작성기 안에서 하므로 EVIDENCE 단계는 따로 두지 않는다.
    """
    operations.update(op, status="RUNNING", stage="SPREAD")
    with session_factory() as db:
        try:
            pid = run_prediction(
                db,
                incident_id=op.incident_id,
                predictor=predictor_from_settings(),
                requested_by=op.user_id,
                weather=weather,
            )
            operations.update(op, stage="RISK")
            _risk_check(db, pid)
            operations.update(op, stage="RULES")
            llm_input = run_engine(
                db,
                incident_id=op.incident_id,
                reader=reader_of(db, op.incident_id),
                prediction_id=pid,
            )
            operations.update(op, stage="PROPOSAL_GENERATION")
            written = writer_from_settings().write(llm_input)
            operations.update(op, stage="COMMIT")
            _check_still_current(db, op.incident_id, perimeter_id)
            proposal_id = save_proposal(
                db, prediction_id=pid, llm_input=llm_input, written=written, user_id=op.user_id
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 작업 실패로 알린다
            db.rollback()
            error = _error_of(exc, op)
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
        op,
        status="SUCCEEDED",
        result={"prediction_id": str(pid), "proposal_id": str(proposal_id)},
    )
