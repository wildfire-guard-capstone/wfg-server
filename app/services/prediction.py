"""예측 돌리기: 현재 화선에서 기상을 받고 예측 제공자를 불러 P1 ~ P8을 저장하고,
그 예측으로 제안서를 만든다."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path

from geoalchemy2.shape import to_shape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agent.clients import LLM, Evidence
from agent.prompt import head_of
from app.api.errors import ApiError
from app.core.config import get_settings
from app.db.models import Incident, Prediction
from app.db.models.enums import IncidentStatus, ProposalTrigger
from app.db.session import SessionLocal
from app.predict import PredictionRequest, Predictor, get_predictor
from app.predict.adj import parse_adj
from app.propose import ProposalWriter, get_writer
from app.propose.base import LlmInput
from app.repositories import base_data, event_logs, incidents, predictions
from app.repositories.engine_inputs import KST, site_of
from app.repositories.proposals import STAGE_OF, reusable_items, save_proposal
from app.services import operations
from app.services.engine import run_engine
from app.services.risk import compute_risk
from app.services.weather import SURFACE, surface_from_settings
from app.weather import Weather, WeatherProvider, WeatherRequest
from app.weather.base import check_weather
from app.weather.elmfire_input import (
    SOURCES,
    ElmfireWeatherInput,
    ElmfireWeatherRequest,
    ElmfireWeatherSource,
    check_input,
    deck_weather_profile,
)

log = logging.getLogger(__name__)
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


def elmfire_source_from_settings(name: str | None = None) -> ElmfireWeatherSource:
    """ELMFIRE 기상 출처를 고른다 — 실시간(stations) ↔ 시연(deck)을 갈아 끼우는 자리.

    name이 없으면 설정 PREDICTOR_WEATHER_FIELD. 예측마다 다른 출처를 쓰려면 이름을 넘긴다
    (나중에 PRED-01 options.weather가 여기로 온다). spread는 쓸 때만 불러온다(API 이미지).
    """
    s = get_settings()
    name = name or s.predictor_weather_field
    if name == "stations":
        if not (s.kma_api_key and s.data_go_kr_api_key):
            raise ValueError("기상 출처 stations에는 KMA_API_KEY · DATA_GO_KR_API_KEY가 필요합니다")
        from app.weather.field import station_field

        return station_field(s.kma_api_key, s.data_go_kr_api_key)
    if name == "deck":
        if not s.predictor_deck:
            raise ValueError("기상 출처 deck에는 PREDICTOR_DECK(덱 폴더)이 필요합니다")
        from app.weather.deck import DeckWeather

        return DeckWeather(Path(s.predictor_deck))
    raise ValueError(f"모르는 기상 출처: {name} (가능: {' · '.join(SOURCES)})")


def build_elmfire_weather(
    session: Session,
    incident_id: int,
    source: ElmfireWeatherSource,
    provider: WeatherProvider | None = None,
) -> ElmfireWeatherInput:
    """현재 화선 기준 ELMFIRE 기상 입력 — 예측 요청 때 만든다(못 만들면 요청이 503).

    1. 0시 = 화선 관측 시각, 발화점 = 저장 · 판정용 9시간을 꺼낼 칸, 격자 = 덱 기상 격자
    2. 출처가 서버 기상을 쓰면(deck: 기온 · 습도 · 강수) provider로 먼저 받는다
    3. 출처로 입력을 만들고 모양을 검사한다
    받지 못하면 WeatherUnavailable, 설정 · 모양이 틀리면 ValueError.
    """
    deck = get_settings().predictor_deck
    if not deck:
        raise ValueError("PREDICTOR=elmfire에는 PREDICTOR_DECK(덱 폴더)이 필요합니다")
    incident = incidents.get_incident(session, incident_id)  # 1
    perimeter = incidents.current_perimeter(session, incident_id)
    base_at = perimeter.observed_at.astimezone(KST)
    site = site_of(to_shape(incident.ignition_point))
    server = None
    if source.uses_server_weather:  # 2
        if provider is None:
            raise ValueError(f"기상 출처 {source.name}에는 서버 기상(WEATHER)이 필요합니다")
        server = fetch_weather(session, incident_id, provider)
    profile = deck_weather_profile(Path(deck))
    inp = source.build(ElmfireWeatherRequest(base_at, site, profile, server))  # 3
    return check_input(inp, profile)


def run_prediction(
    session: Session,
    *,
    incident_id: int,
    predictor: Predictor,
    requested_by: int,
    weather: Weather | None = None,
    elmfire_weather: ElmfireWeatherInput | None = None,
    mode: str | None = None,
) -> int:
    """예측 id를 돌려준다. 커밋은 부르는 쪽이 한다. mode(PRED-01)는 이벤트 로그에 남긴다.

    1. 현재 화선(없으면 v0 발화점) = P0, 그 관측 시각 = 0시
    2. 예측 제공자에 넘긴다 — 어떤 예측이든 같은 약속(app.predict.base). 받은 기상(weather)이
       있으면 같이 넘겨 그 기상으로 계산한다(없으면 예측 제공자의 고정값 — 시험 · 개발 명령).
       ELMFIRE는 요청 때 만든 기상 입력(elmfire_weather)을 쓴다
    3. 검사(8개 · 면 · 누적) 후 저장. 기상은 예측에 실제로 쓴 값(결과의 weather) →
       규칙 엔진 · 제안서가 예측과 같은 기상을 쓴다
    """
    incident = incidents.get_incident(session, incident_id)
    perimeter = incidents.current_perimeter(session, incident_id)  # 1
    start = to_shape(perimeter.geom)
    base_at = perimeter.observed_at.astimezone(KST)
    site = site_of(to_shape(incident.ignition_point))
    request = PredictionRequest(start, base_at, site, weather, elmfire_weather)
    result = predictor.predict(request)  # 2
    # 위험도용 지상관측(기압 · 지면온도 · 7일 강수)을 같이 남긴다(고정 시험값이면 받지 않음)
    if result.weather.get("source") != "FIXED" and (surface := surface_from_settings(base_at)):
        result = replace(result, weather={**result.weather, SURFACE: surface})
    if mode:
        result = replace(result, meta={**result.meta, "mode": mode})
    return predictions.save_prediction(  # 3
        session,
        incident_id=incident_id,
        perimeter_id=perimeter.id,
        start=start,
        base_at=base_at,
        result=result,
        requested_by=requested_by,
    )


# 예측 모드(PRED-01 mode, app.schemas.prediction.PredictionMode) → ELMFIRE 기상 출처.
# 모드가 없으면 서버 기본 설정(PREDICTOR · WEATHER · PREDICTOR_WEATHER_FIELD)을 따른다.
MODE_SOURCES = {"LIVE": "stations", "DEMO_ELMFIRE": "deck"}


def elmfire_predictor_from_settings() -> Predictor:
    """ELMFIRE 예측. 구조 B(EC2): ELMFIRE_WORKER_URL이 있으면 worker 컨테이너로 보내고,
    없으면 같은 프로세스에서 돌린다(GPU 서버 — PREDICTOR_DECK · ELMFIRE_BIN)."""
    s = get_settings()
    if s.elmfire_worker_url:
        from app.predict.elmfire_remote import ElmfireWorker

        return ElmfireWorker(s.elmfire_worker_url, s.elmfire_worker_timeout_s)
    if not s.predictor_deck:
        raise ValueError("ELMFIRE 예측에는 PREDICTOR_DECK(덱 폴더)이 필요합니다")
    options = {"deck": s.predictor_deck, "elmfire": s.elmfire_bin, "adj": parse_adj(s.elmfire_adj)}
    if s.predictor_work_dir:
        options["work_dir"] = s.predictor_work_dir
    return get_predictor("elmfire", **options)


def predictor_from_settings(mode: str | None = None) -> Predictor:
    """예측 제공자를 고른다 — 팀원이 갈아 끼우는 자리.

    모드(LIVE · DEMO_ELMFIRE)가 있으면 ELMFIRE, 없으면 설정(PREDICTOR · PREDICTOR_PATH …).
    """
    s = get_settings()
    if mode in MODE_SOURCES or s.predictor == "elmfire":
        return elmfire_predictor_from_settings()
    options = {}
    if s.predictor == "file":
        options = {"path": s.predictor_path, "crs": s.predictor_crs}
        if s.predictor_weather:
            options["weather_path"] = s.predictor_weather
    return get_predictor(s.predictor, **options)


def writer_from_settings() -> ProposalWriter:
    """설정(PROPOSAL_WRITER)으로 제안 작성기를 고른다 — 가짜 ↔ 에이전트를 갈아 끼우는 자리.
    agent는 GPU 서버의 vLLM(LLM_URL) · 근거 서버(EVIDENCE_URL)를 부른다."""
    s = get_settings()
    if s.proposal_writer != "agent":
        return get_writer(s.proposal_writer)
    if not s.evidence_url:
        raise ValueError("PROPOSAL_WRITER=agent에는 EVIDENCE_URL(근거 서버 주소)이 필요합니다")
    return get_writer(
        "agent",
        llm=LLM(url=s.llm_url, model=s.llm_model, timeout=s.proposal_llm_timeout_s),
        evidence=Evidence(url=s.evidence_url),
        names=known_names,
    )


def known_names() -> set[str]:
    """제안 문장의 이름 검사(agent.check)에 쓰는 지명 목록 — 작성기가 쓸 때 부른다."""
    with SessionLocal() as db:
        return base_data.known_names(db)


def reader_of(db: Session, incident_id: int) -> str:
    """제안서를 읽는 사람 = 산불이 난 시군구의 단체장(의성군 → 의성군수).
    계정에는 직함 칸이 없다."""
    sigungu, _ = incidents.incident_names(db, incidents.get_incident(db, incident_id))
    return head_of(sigungu)


def _check_still_current(db: Session, incident_id: int, perimeter_id: int) -> None:
    """예측 저장 직전 재검사: 그사이 종료되거나 화선이 바뀌었으면 옛 예측을 저장하지 않는다."""
    incident = db.get(Incident, incident_id)
    db.refresh(incident)
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "계산하는 동안 산불이 종료되었습니다.")
    if incidents.current_perimeter(db, incident_id).id != perimeter_id:
        raise ApiError(409, "STALE_PERIMETER", "계산하는 동안 화선이 바뀌었습니다.")


def _judge(db: Session, incident_id: int, prediction_id: int) -> tuple[LlmInput | None, int | None]:
    """규칙 엔진을 돌려 산불 대응단계를 정한다(예측과 같이 저장 — 제안서를 기다리지 않고 보이게).
    (엔진 결과, 그때 본 최신 정정 id). 엔진이 실패하면 결과는 None: 예측은 그대로 저장하고,
    제안서 작업이 엔진을 다시 돌려 그 실패를 알린다."""
    seen = _correction_id(db, incident_id)  # 상황을 읽기 전 — 그 뒤 들어온 정정을 놓치지 않게
    try:
        with db.begin_nested():
            llm_input = run_engine(
                db,
                incident_id=incident_id,
                reader=reader_of(db, incident_id),
                prediction_id=prediction_id,
            )
            stage = llm_input["bundles"]["stage"]["now"]["stage"]
            db.get(Incident, incident_id).current_stage = STAGE_OF[stage]
        return llm_input, seen
    except Exception:  # noqa: BLE001
        log.exception("규칙 엔진 실패(예측은 저장) — 예측 %s", prediction_id)
        return None, seen


def _correction_id(db: Session, incident_id: int) -> int | None:
    last = incidents.latest_correction(db, incident_id)
    return last[0] if last else None


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
    return {"code": "INTERNAL_ERROR", "message": "예측 중 오류가 발생했습니다."}


def run_prediction_operation(
    op: operations.Operation,
    perimeter_id: int,
    session_factory: SessionFactory,
    weather: Weather | None = None,
    elmfire_weather: ElmfireWeatherInput | None = None,
    mode: str | None = None,
) -> None:
    """PRED-01 작업 = 두 묶음.

    1. 예측(SPREAD) → 위험도(RISK) → 규칙 엔진의 대응단계(RULES) → 재검사 · 저장(COMMIT).
       여기서 예측 작업은 성공으로 끝나고 화면은 지도 · 위험도 · 단계를 바로 보인다
    2. 제안서 만들기(run_proposal_operation) — 새 작업(result.proposal_operation_id). 실패해도
       예측은 남고, 그 예측으로 다시 만든다(PROP-05)

    기상(weather · ELMFIRE면 elmfire_weather)은 예측 요청 때 받아 넘긴다(못 받으면 요청이 503
    WEATHER_UNAVAILABLE — 작업은 시작하지 않음). mode(LIVE · DEMO_ELMFIRE · 없음 = 서버 기본)로
    예측을 고르고, 성공하면 result에 쓴 모드 · 예측 이름을 남긴다.
    1에서 실패하면 예측을 남기지 않고(직전 예측 · 제안서가 최신) PREDICTION_FAILED만 기록한다.
    """
    operations.update(op, status="RUNNING", stage="SPREAD")
    with session_factory() as db:
        try:
            predictor = predictor_from_settings(mode)
            pid = run_prediction(
                db,
                incident_id=op.incident_id,
                predictor=predictor,
                requested_by=op.user_id,
                weather=weather,
                elmfire_weather=elmfire_weather,
                mode=mode,
            )
            operations.update(op, stage="RISK")
            _risk_check(db, pid)
            operations.update(op, stage="RULES")
            llm_input, seen = _judge(db, op.incident_id, pid)
            operations.update(op, stage="COMMIT")
            _check_still_current(db, op.incident_id, perimeter_id)
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
    follow = operations.hand_over(
        op,
        "PROPOSAL_REGENERATION",
        {
            "prediction_id": str(pid),
            "mode": mode,  # 요청한 모드, null = 서버 기본 설정
            "predictor": predictor.name,  # 실제로 쓴 예측(synthetic · file · elmfire)
        },
    )
    run_proposal_operation(
        follow,
        prediction_id=pid,
        session_factory=session_factory,
        llm_input=llm_input,
        seen_correction_id=seen,
    )


def run_proposal_operation(
    op: operations.Operation,
    *,
    prediction_id: int,
    session_factory: SessionFactory,
    correction_id: int | None = None,
    llm_input: LlmInput | None = None,
    seen_correction_id: int | None = None,
) -> None:
    """제안서 만들기: 규칙 엔진(RULES) → 제안 작성(PROPOSAL_GENERATION) → 저장(COMMIT).

    예측 뒤(correction_id 없음, 예측 때 돌린 엔진 결과 llm_input을 그대로 씀)와 상황 정정 뒤
    (correction_id, 같은 예측 + 지금 상황)가 같이 쓴다. 저장 직전에 종료 · 새 예측이 있으면 저장하지
    않고, 정정 뒤라면 더 새로운 정정이 있어도 저장하지 않는다. 그사이 새 화선이 보고된 것은 막지
    않는다 — 제안서는 제 예측(화선 버전)에 딸리고, 화면은 「재예측 필요」를 보인다.
    실패하면 PROPOSAL_FAILED만 남기고 예측 · 정정은 그대로다.
    """
    operations.update(op, status="RUNNING", stage="RULES")
    with session_factory() as db:
        try:
            # 트랜잭션을 지금 연다 → 제안서 created_at = 만들기 시작한 때. 만드는 동안 들어온 정정은
            # 그보다 늦으므로 proposal_stale이 되어 다시 만들 수 있다(끝난 때로 찍히면 놓친다)
            db.execute(select(func.now()))
            # 예측 때 엔진을 돌린 뒤 정정이 들어왔으면 그 상황으로 엔진을 다시 돌린다
            if llm_input is None or _correction_id(db, op.incident_id) != seen_correction_id:
                llm_input = run_engine(
                    db,
                    incident_id=op.incident_id,
                    reader=reader_of(db, op.incident_id),
                    prediction_id=prediction_id,
                )
            operations.update(op, stage="PROPOSAL_GENERATION")
            # 상황 정정 뒤(같은 예측): 입력이 그대로인 항목은 직전 제안을 그대로 쓴다
            prev = reusable_items(db, prediction_id) if correction_id is not None else None
            written = writer_from_settings().write(llm_input, prev)
            operations.update(op, stage="COMMIT")
            incident = db.get(Incident, op.incident_id)
            db.refresh(incident)
            if incident.status is IncidentStatus.ENDED:
                raise ApiError(409, "INCIDENT_ENDED", "계산하는 동안 산불이 종료되었습니다.")
            latest = incidents.latest_prediction(db, op.incident_id)
            if latest is None or latest[0] != prediction_id:
                raise ApiError(409, "STALE_PREDICTION", "계산하는 동안 새 예측이 생겼습니다.")
            last = incidents.latest_correction(db, op.incident_id)
            if correction_id is not None and last is not None and last[0] != correction_id:
                raise ApiError(409, "STALE_SITUATION", "계산하는 동안 다른 정정이 들어왔습니다.")
            after_correction = correction_id is not None
            proposal_id = save_proposal(
                db,
                prediction_id=prediction_id,
                llm_input=llm_input,
                written=written,
                user_id=op.user_id,
                trigger=ProposalTrigger.CORRECTION
                if after_correction
                else ProposalTrigger.PREDICTION,
                correction_id=correction_id,
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 작업 실패로 알린다
            db.rollback()
            if isinstance(exc, ApiError):
                error = {"code": exc.code, "message": exc.message}
            else:
                log.exception("제안서 만들기 실패 (%s, %s 단계)", op.id, op.stage)
                error = {
                    "code": "PROPOSAL_FAILED",
                    "message": "제안서를 만드는 중 오류가 발생했습니다.",
                }
            event_logs.add(
                db,
                "PROPOSAL_FAILED",
                user_id=op.user_id,
                incident_id=op.incident_id,
                details={
                    "operation_id": op.id,
                    "prediction_id": prediction_id,
                    "correction_id": correction_id,
                    **error,
                },
            )
            db.commit()
            operations.update(op, status="FAILED", error=error)
            return
    operations.update(
        op,
        status="SUCCEEDED",
        result={
            "proposal_id": str(proposal_id),
            "prediction_id": str(prediction_id),
            "correction_id": str(correction_id) if correction_id is not None else None,
        },
    )
