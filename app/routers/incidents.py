"""INC-01 목록 · INC-02 보고 · INC-03 상세 · INC-04 종료,
PER-01 화선 버전 목록 · PER-02 실측 화선 보고 · PER-03 현재 실측 화선 정정."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Path, Request
from geoalchemy2.shape import to_shape
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import Db, require_roles
from app.api.envelope import Envelope, Page, ok
from app.api.errors import ApiError, not_found
from app.api.geo import from_geojson, to_geojson
from app.api.pagination import LIMIT, cut, decode_cursor
from app.db.models import FirePerimeter, Incident, User
from app.db.models.enums import IncidentStatus
from app.repositories import event_logs, incidents, resources
from app.repositories.predictions import to_multipolygon
from app.schemas.incident import (
    IncidentCreateIn,
    IncidentEndIn,
    IncidentOut,
    IncidentSummaryOut,
    PerimeterCorrectionIn,
    PerimeterOut,
    PerimeterReportIn,
    StatusName,
)
from app.services import operations

router = APIRouter(prefix="/incidents", tags=["산불 · 보고"])
Reader = require_roles("COMMANDER", "VIEWER", "REPORTER")
# INC-01만 관리자도: 자원을 「투입」으로 바꿀 때 진행 중 산불을 고른다(04 수정 필요 2번)
Lister = require_roles("COMMANDER", "VIEWER", "REPORTER", "ADMIN")
Reporter = require_roles("REPORTER")
Commander = require_roles("COMMANDER")
IncidentId = Annotated[int, Path(gt=0)]


def load_incident(db: Session, user: User, incident_id: int) -> Incident:
    """산불을 읽고 관할을 확인한다. 없거나 관할 밖이면 404(명세 2.3 — 범위 밖은 숨김)."""
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise not_found("산불을 찾을 수 없습니다.")
    if user.admin_area_id is not None and incidents.sigungu_of(db, incident) != user.admin_area_id:
        raise not_found("산불을 찾을 수 없습니다.")
    return incident


def _refs(db: Session, incident: Incident) -> dict:
    """현재 화선 · 최신 예측 · 제안 참조와 갱신 안내 값."""
    perimeter = incidents.current_perimeter(db, incident.id)
    latest = incidents.latest_prediction(db, incident.id)
    proposal = incidents.latest_proposal(db, incident.id)
    correction = incidents.latest_correction(db, incident.id)
    stale = bool(proposal and correction and correction[1] > proposal.created_at)
    return {
        "perimeter": perimeter,
        "latest_prediction_id": latest[0] if latest else None,
        "latest_proposal_id": proposal.id if proposal else None,
        "needs_reprediction": latest is None or perimeter.version_no > latest[1],
        "proposal_stale": stale,
        # 동시 수정 방지 토큰(명세 2.4 제안). 지금은 최신 정정 id — 방식은 Q-05 · Q-06
        "situation_revision": incidents.situation_revision(db, incident.id),
    }


def incident_out(db: Session, incident: Incident) -> IncidentOut:
    refs = _refs(db, incident)
    perimeter = refs.pop("perimeter")
    return IncidentOut(
        id=incident.id,
        name=incidents.incident_name(db, incident),
        ignition_point=to_geojson(to_shape(incident.ignition_point)),
        address=incident.address,
        admin_area_id=incident.admin_area_id,
        started_at=incident.started_at,
        reported_at=incident.reported_at,
        report_text=incident.report_text,
        reporter_org=incident.reporter_org,
        reporter_name=incident.reporter_name,
        reporter_phone=incident.reporter_phone,
        status=incident.status.name,
        current_stage=incident.current_stage.name,
        situation=incident.situation,
        reported_by=incident.reported_by,
        created_at=incident.created_at,
        ended_at=incident.ended_at,
        ended_by=incident.ended_by,
        current_perimeter_id=perimeter.id,
        current_perimeter_version=perimeter.version_no,
        running_operation_id=op.id if (op := operations.running(incident.id)) else None,
        **refs,
    )


def summary_out(db: Session, incident: Incident) -> IncidentSummaryOut:
    refs = _refs(db, incident)
    return IncidentSummaryOut(
        id=incident.id,
        name=incidents.incident_name(db, incident),
        status=incident.status.name,
        admin_area_id=incident.admin_area_id,
        reported_at=incident.reported_at,
        current_stage=incident.current_stage.name,
        current_perimeter_version=refs["perimeter"].version_no,
        needs_reprediction=refs["needs_reprediction"],
        proposal_stale=refs["proposal_stale"],
    )


def perimeter_out(p: FirePerimeter) -> PerimeterOut:
    geom = to_shape(p.geom)
    return PerimeterOut(
        id=p.id,
        incident_id=p.incident_id,
        version_no=p.version_no,
        geometry=to_geojson(geom),
        observed_at=p.observed_at,
        reported_by=p.reported_by,
        created_at=p.created_at,
        corrected_at=p.corrected_at,
        area_ha=round(geom.area / 10_000, 2),
    )


@router.get("", response_model=Envelope[Page[IncidentSummaryOut]])
def list_incidents(
    request: Request,
    user: Lister,
    db: Db,
    status: StatusName | None = None,
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """INC-01. 관할 시군구 산불만(관리자는 관할이 없어 전체 지역), 접수 시각 · id 내림차순."""
    after = decode_cursor(cursor, 2)
    rows = incidents.list_incidents(
        db,
        status=IncidentStatus[status] if status else None,
        sigungu_id=user.admin_area_id,
        after=(datetime.fromisoformat(after[0]), int(after[1])) if after else None,
        limit=limit,
    )
    items, next_cursor, has_more = cut(rows, limit, lambda i: (i.reported_at.isoformat(), i.id))
    page = Page(
        items=[summary_out(db, i) for i in items], next_cursor=next_cursor, has_more=has_more
    )
    return ok(request, page)


@router.post("", status_code=201, response_model=Envelope[IncidentOut])
def report_incident(
    body: IncidentCreateIn,
    request: Request,
    user: Reporter,
    db: Db,
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
):
    """INC-02. 산불 + 화선 v0(발화점), 실측 화선을 함께 보내면 v1까지 한 번에 저장.

    Idempotency-Key(명세 2.4): 같은 보고자가 같은 번호 · 같은 본문을 다시 보내면(통신 재전송)
    새로 만들지 않고 처음 만든 산불을 돌려준다. 같은 번호에 다른 본문은 409. 번호 없이 보내면
    중복을 막지 않는다.
    """
    request_hash = None
    if idempotency_key:
        request_hash = _hash(body)
        done = _replayed(db, user.id, idempotency_key, request_hash)
        if done is not None:
            return ok(request, incident_out(db, done))
    point = from_geojson(body.ignition_point, ("Point",), "ignition_point")
    first = None
    if body.initial_perimeter:
        geom = from_geojson(
            body.initial_perimeter.geometry, ("Polygon", "MultiPolygon"), "initial_perimeter"
        )
        first = (to_multipolygon(geom), body.initial_perimeter.observed_at)
    try:
        emd = incidents.emd_at(db, point)
    except ValueError as exc:
        raise ApiError(
            422, "OUTSIDE_JURISDICTION", "발화 지점이 서비스 범위(의성 + 인근 10 km) 밖입니다."
        ) from exc
    if user.admin_area_id is not None and incidents.parent_of(db, emd) != user.admin_area_id:
        raise ApiError(422, "OUTSIDE_JURISDICTION", "관할 밖의 산불입니다.")
    try:
        incident_id = incidents.create_incident(
            db,
            ignition=point,
            address=body.address,
            reported_at=body.reported_at,
            started_at=body.started_at,
            reporter_name=body.reporter_name,
            reporter_phone=body.reporter_phone,
            report_text=body.report_text,
            reporter_org=body.reporter_org,
            reported_by=user.id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
    except IntegrityError:  # 같은 번호가 거의 동시에 두 번 → 먼저 저장된 것을 돌려줌
        db.rollback()
        done = _replayed(db, user.id, idempotency_key, request_hash) if idempotency_key else None
        if done is None:
            raise
        return ok(request, incident_out(db, done))
    event_logs.add(db, "INCIDENT_REPORTED", user_id=user.id, incident_id=incident_id)
    if first:
        p = incidents.add_perimeter(
            db, incident_id=incident_id, geom=first[0], observed_at=first[1], reported_by=user.id
        )
        details = {"perimeter_id": p.id, "version_no": p.version_no}
        event_logs.add(
            db, "PERIMETER_REPORTED", user_id=user.id, incident_id=incident_id, details=details
        )
    db.commit()
    return ok(request, incident_out(db, db.get(Incident, incident_id)))


def _hash(body: IncidentCreateIn) -> str:
    raw = json.dumps(body.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _replayed(db: Session, user_id: int, key: str, request_hash: str) -> Incident | None:
    """같은 번호로 이미 만든 산불. 본문이 다르면 409(앱이 번호를 잘못 다시 씀)."""
    done = incidents.reported_with_key(db, user_id, key)
    if done is not None and done.request_hash != request_hash:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 요청 번호로 다른 내용을 보냈습니다.")
    return done


@router.get("/{incident_id}", response_model=Envelope[IncidentOut])
def get_incident(incident_id: IncidentId, request: Request, user: Reader, db: Db):
    """INC-03."""
    return ok(request, incident_out(db, load_incident(db, user, incident_id)))


@router.post("/{incident_id}/end", response_model=Envelope[IncidentOut])
def end_incident(
    incident_id: IncidentId, body: IncidentEndIn, request: Request, user: Commander, db: Db
):
    """INC-04. 진행 중 산불만, 화면이 본 현재 화선과 같을 때만 종료한다. 이 산불에 투입된
    자원은 대기로 돌리고 연결을 푼다(명세 v0.5) — 다른 산불에서 다시 부를 수 있게."""
    incident = load_incident(db, user, incident_id)
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "이미 종료된 산불입니다.")
    if incidents.current_perimeter(db, incident_id).id != body.expected_perimeter_id:
        raise ApiError(409, "STALE_PERIMETER", "화선이 바뀌었습니다. 새로 고친 뒤 다시 하세요.")
    if operations.is_running(incident_id):
        raise ApiError(409, "RESOURCE_BUSY", "예측을 계산하는 중입니다. 끝난 뒤 종료하세요.")
    incident.status = IncidentStatus.ENDED
    incident.ended_at = datetime.now(UTC)
    incident.ended_by = user.id
    released = resources.release_linked(db, incident_id)
    for r in released:  # 자원 이력은 관리자 수정과 같은 이름(RESOURCE_UPDATED)에 op로 구분
        event_logs.add(
            db,
            "RESOURCE_UPDATED",
            user_id=user.id,
            incident_id=incident_id,
            details={
                "op": "released",
                "resource_id": r.id,
                "name": r.name,
                "changed": ["status", "deployed_incident_id"],
            },
        )
    event_logs.add(
        db,
        "INCIDENT_ENDED",
        user_id=user.id,
        incident_id=incident_id,
        details={"released_resource_ids": [r.id for r in released]},
    )
    db.commit()
    return ok(request, incident_out(db, incident))


@router.get("/{incident_id}/perimeters", response_model=Envelope[Page[PerimeterOut]])
def list_perimeters(
    incident_id: IncidentId,
    request: Request,
    user: Reader,
    db: Db,
    cursor: str | None = None,
    limit: int = LIMIT,
):
    """PER-01. 버전 내림차순, v0 · 정정된 버전도 포함."""
    load_incident(db, user, incident_id)
    after = decode_cursor(cursor, 1)
    rows = incidents.list_perimeters(
        db, incident_id, before_version=int(after[0]) if after else None, limit=limit
    )
    items, next_cursor, has_more = cut(rows, limit, lambda p: (p.version_no,))
    page = Page(items=[perimeter_out(p) for p in items], next_cursor=next_cursor, has_more=has_more)
    return ok(request, page)


FUTURE_SLACK_S = 300  # 관측 시각이 지금보다 이만큼 넘게 미래면 거절(기기 시계 차이는 허용)


def _reject_future(observed_at: datetime) -> None:
    """화선 보고(PER-02) · 정정(PER-03) 공통: 5분 넘게 미래인 관측 시각은 거절(11/11 확정)."""
    if (observed_at - datetime.now(UTC)).total_seconds() > FUTURE_SLACK_S:
        raise ApiError(
            422,
            "INVALID_OBSERVED_AT",
            "미래 시각입니다.",
            [{"field": "observed_at", "reason": "지금보다 늦을 수 없습니다."}],
        )


def _checked_perimeter(db: Session, incident: Incident, geometry: dict, observed_at: datetime):
    """새 화선 도형 · 관측 시각 검사: 면(Polygon · MultiPolygon) · 올바른 도형, 현재 화선보다
    앞선 시각이나 미래 시각은 거절(예측 0시가 거꾸로 가지 않게)."""
    geom = to_multipolygon(from_geojson(geometry, ("Polygon", "MultiPolygon"), "geometry"))
    current = incidents.current_perimeter(db, incident.id)
    if observed_at < current.observed_at:
        raise ApiError(
            422,
            "INVALID_OBSERVED_AT",
            "현재 화선보다 앞선 관측 시각입니다.",
            [{"field": "observed_at", "reason": "현재 화선 관측 시각 이후여야 합니다."}],
        )
    _reject_future(observed_at)
    return geom


@router.post("/{incident_id}/perimeters", status_code=201, response_model=Envelope[PerimeterOut])
def report_perimeter(
    incident_id: IncidentId,
    body: PerimeterReportIn,
    request: Request,
    user: Reporter,
    db: Db,
    idempotency_key: Annotated[str | None, Header()] = None,
):
    """PER-02. 새 실측 화선을 다음 버전으로 저장한다. 예측은 자동으로 돌리지 않는다
    (산불 상세 needs_reprediction = true). 대응단계는 예측할 때 판정한다(11/9 결정 — 명세의
    「화선 보고 때 단계 재판정」과 다름). Idempotency-Key는 받기만 한다."""
    incident = load_incident(db, user, incident_id)
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "종료된 산불입니다.")
    if incidents.current_perimeter(db, incident_id).id != body.expected_perimeter_id:
        raise ApiError(409, "STALE_PERIMETER", "화선이 바뀌었습니다. 새로 고친 뒤 다시 하세요.")
    geom = _checked_perimeter(db, incident, body.geometry, body.observed_at)
    p = incidents.add_perimeter(
        db, incident_id=incident_id, geom=geom, observed_at=body.observed_at, reported_by=user.id
    )
    event_logs.add(
        db,
        "PERIMETER_REPORTED",
        user_id=user.id,
        incident_id=incident_id,
        details={"perimeter_id": p.id, "version_no": p.version_no},
    )
    db.commit()
    return ok(request, perimeter_out(p))


@router.post(
    "/{incident_id}/perimeters/{perimeter_id}/corrections",
    status_code=201,
    response_model=Envelope[PerimeterOut],
)
def correct_perimeter(
    incident_id: IncidentId,
    perimeter_id: Annotated[int, Path(gt=0)],
    body: PerimeterCorrectionIn,
    request: Request,
    user: Reporter,
    db: Db,
    idempotency_key: Annotated[str | None, Header()] = None,
):
    """PER-03. 현재 실측 화선(v1 이상)만 정정한다: 그 행에 corrected_at을 찍고 고친 도형을
    다음 버전으로 넣는다(덮어쓰지 않음). 지난 예측 · 제안서는 그대로 둔다."""
    incident = load_incident(db, user, incident_id)
    if incident.status is IncidentStatus.ENDED:
        raise ApiError(409, "INCIDENT_ENDED", "종료된 산불입니다.")
    current = incidents.current_perimeter(db, incident_id)
    target = db.get(FirePerimeter, perimeter_id)
    if target is None or target.incident_id != incident_id:
        raise not_found("화선을 찾을 수 없습니다.")
    if target.version_no == 0:
        raise ApiError(422, "V0_NOT_CORRECTABLE", "발화점(v0)은 정정할 수 없습니다.")
    if target.id != current.id:
        raise ApiError(409, "STALE_PERIMETER", "현재 화선만 정정할 수 있습니다.")
    previous = incidents.current_perimeter(db, incident_id, before_version=target.version_no)
    geom = to_multipolygon(from_geojson(body.geometry, ("Polygon", "MultiPolygon"), "geometry"))
    if body.observed_at < previous.observed_at:
        raise ApiError(
            422,
            "INVALID_OBSERVED_AT",
            "직전 화선보다 앞선 관측 시각입니다.",
            [{"field": "observed_at", "reason": "직전 화선 관측 시각 이후여야 합니다."}],
        )
    _reject_future(body.observed_at)
    target.corrected_at = datetime.now(UTC)
    p = incidents.add_perimeter(
        db, incident_id=incident_id, geom=geom, observed_at=body.observed_at, reported_by=user.id
    )
    event_logs.add(
        db,
        "PERIMETER_CORRECTED",
        user_id=user.id,
        incident_id=incident_id,
        details={"corrected_id": target.id, "perimeter_id": p.id, "version_no": p.version_no},
    )
    db.commit()
    return ok(request, perimeter_out(p))
