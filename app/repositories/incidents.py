"""산불 · 화선 · 계정 · 직전 제안서 읽기 · 쓰기(DB 설계 2.1 · 2.2 · 2.4)."""

from datetime import datetime
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.time import KST
from app.db.models import AdminArea, FirePerimeter, Incident, Prediction, Proposal, User
from app.db.models._types import SRID
from app.db.models.enums import IncidentStatus, UserRole


def get_or_create_user(
    session: Session, login_id: str, name: str, role: UserRole, organization: str = "개발용"
) -> int:
    """개발 · 테스트용 계정. 있으면 그 id(대소문자 무시), 없으면 만든다(비밀번호 로그인 불가 값)."""
    user_id = session.scalar(select(User.id).where(func.lower(User.login_id) == login_id.lower()))
    if user_id is not None:
        return user_id
    user = User(
        login_id=login_id, password_hash="!", name=name, organization=organization, role=role
    )
    session.add(user)
    session.flush()
    return user.id


def emd_at(session: Session, point: BaseGeometry) -> int:
    """점이 든 읍면동 id(산불 등록 때 admin_area_id, DB 설계 2.6)."""
    area_id = session.scalar(
        text(
            "SELECT id FROM admin_areas WHERE level = 'EMD'"
            " AND ST_Contains(geom, ST_GeomFromText(:wkt, :srid)) LIMIT 1"
        ),
        {"wkt": point.wkt, "srid": SRID},
    )
    if area_id is None:
        raise ValueError("발화 지점이 든 읍면동이 없습니다(바탕 데이터 범위 밖)")
    return area_id


def create_incident(
    session: Session,
    *,
    ignition: Point,
    address: str,
    reported_at: datetime,
    reported_by: int,
    started_at: datetime | None = None,
    reporter_name: str = "미상",
    reporter_phone: str = "미상",
    report_text: str | None = None,
    reporter_org: str | None = None,
    situation: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    request_hash: str | None = None,
) -> int:
    """산불 1행 + 화선 v0(발화점)을 함께 넣는다(DB 설계 2.2). 커밋은 부르는 쪽이 한다."""
    point = from_shape(ignition, srid=SRID)
    incident = Incident(
        ignition_point=point,
        address=address,
        admin_area_id=emd_at(session, ignition),
        started_at=started_at,
        reported_at=reported_at,
        reporter_name=reporter_name,
        reporter_phone=reporter_phone,
        report_text=report_text,
        reporter_org=reporter_org,
        situation=situation or {},
        reported_by=reported_by,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    session.add(incident)
    session.flush()
    session.add(
        FirePerimeter(
            incident_id=incident.id,
            version_no=0,
            geom=point,
            observed_at=started_at or reported_at,
            reported_by=reported_by,
        )
    )
    session.flush()
    return incident.id


def reported_with_key(session: Session, reported_by: int, key: str) -> Incident | None:
    """같은 보고자가 같은 요청 번호로 이미 만든 산불(INC-02 재전송)."""
    return session.scalar(
        select(Incident).where(Incident.reported_by == reported_by, Incident.idempotency_key == key)
    )


def get_incident(session: Session, incident_id: int) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"산불 {incident_id}이 없습니다")
    return incident


def current_perimeter(
    session: Session, incident_id: int, *, before_version: int | None = None
) -> FirePerimeter:
    """현재 화선 = 정정되지 않은 것 중 버전이 가장 큰 행(없으면 v0 발화점).
    before_version을 주면 그 버전보다 앞선 것 중에서(정정할 화선의 직전 화선)."""
    q = (
        select(FirePerimeter)
        .where(FirePerimeter.incident_id == incident_id, FirePerimeter.corrected_at.is_(None))
        .order_by(FirePerimeter.version_no.desc())
        .limit(1)
    )
    if before_version is not None:
        q = q.where(FirePerimeter.version_no < before_version)
    perimeter = session.scalar(q)
    if perimeter is None:
        raise ValueError(f"산불 {incident_id}에 화선(v0 발화점)이 없습니다")
    return perimeter


def perimeter_shape(session: Session, perimeter_id: int) -> BaseGeometry:
    return to_shape(session.get(FirePerimeter, perimeter_id).geom)


def latest_proposal(session: Session, incident_id: int) -> Proposal | None:
    """직전 제안서 = 그 산불에서 id가 가장 큰 행(DB 설계 2.4)."""
    return session.scalar(
        select(Proposal)
        .where(Proposal.incident_id == incident_id)
        .order_by(Proposal.id.desc())
        .limit(1)
    )


def incident_names(session: Session, incident: Incident) -> tuple[str, str]:
    """(시군구, 읍면동) 이름 — 산불 이름 「의성군 안평면 산불 (2025-03-22)」에 쓴다."""
    row = session.execute(
        text(
            "SELECT s.name, e.name FROM admin_areas e JOIN admin_areas s ON s.id = e.parent_id"
            " WHERE e.id = :id"
        ),
        {"id": incident.admin_area_id},
    ).one()
    return row[0], row[1]


def incident_name(session: Session, incident: Incident) -> str:
    """「의성군 안평면 산불 (2025-03-22)」 — 발생 날짜, 없으면 접수 날짜(한국 시간)."""
    sigungu, emd = incident_names(session, incident)
    day = (incident.started_at or incident.reported_at).astimezone(KST)
    return f"{sigungu} {emd} 산불 ({day:%Y-%m-%d})"


def parent_of(session: Session, admin_area_id: int) -> int | None:
    """행정구역의 상위 id(읍면동 → 시군구)."""
    return session.scalar(
        text("SELECT parent_id FROM admin_areas WHERE id = :id"), {"id": admin_area_id}
    )


def sigungu_of(session: Session, incident: Incident) -> int | None:
    """산불(발화 읍면동)의 시군구 id — 관할 비교용."""
    return parent_of(session, incident.admin_area_id)


def list_incidents(
    session: Session,
    *,
    status: IncidentStatus | None,
    sigungu_id: int | None,
    after: tuple[datetime, int] | None,
    limit: int,
) -> list[Incident]:
    """접수 시각 · id 내림차순, limit + 1개. sigungu_id가 있으면 그 관할 산불만."""
    q = select(Incident).order_by(Incident.reported_at.desc(), Incident.id.desc()).limit(limit + 1)
    if status is not None:
        q = q.where(Incident.status == status)
    if sigungu_id is not None:
        emds = select(AdminArea.id).where(AdminArea.parent_id == sigungu_id)
        q = q.where(Incident.admin_area_id.in_(emds))
    if after is not None:
        at, last_id = after
        q = q.where(
            (Incident.reported_at < at) | ((Incident.reported_at == at) & (Incident.id < last_id))
        )
    return list(session.scalars(q))


def add_perimeter(
    session: Session,
    *,
    incident_id: int,
    geom: BaseGeometry,
    observed_at: datetime,
    reported_by: int,
) -> FirePerimeter:
    """실측 화선을 다음 버전으로 넣는다(정정도 덮어쓰지 않고 새 버전)."""
    last = session.scalar(
        select(func.max(FirePerimeter.version_no)).where(FirePerimeter.incident_id == incident_id)
    )
    perimeter = FirePerimeter(
        incident_id=incident_id,
        version_no=(last or 0) + 1,
        geom=from_shape(geom, srid=SRID),
        observed_at=observed_at,
        reported_by=reported_by,
    )
    session.add(perimeter)
    session.flush()
    return perimeter


def list_perimeters(
    session: Session, incident_id: int, *, before_version: int | None, limit: int
) -> list[FirePerimeter]:
    """버전 내림차순(정정된 버전 · v0 포함), limit + 1개."""
    q = (
        select(FirePerimeter)
        .where(FirePerimeter.incident_id == incident_id)
        .order_by(FirePerimeter.version_no.desc())
        .limit(limit + 1)
    )
    if before_version is not None:
        q = q.where(FirePerimeter.version_no < before_version)
    return list(session.scalars(q))


def latest_prediction(session: Session, incident_id: int) -> tuple[int, int] | None:
    """(최신 예측 id, 그 예측의 시작 화선 버전). 예측이 없으면 None."""
    row = session.execute(
        select(Prediction.id, FirePerimeter.version_no)
        .join(FirePerimeter, FirePerimeter.id == Prediction.perimeter_id)
        .where(Prediction.incident_id == incident_id)
        .order_by(Prediction.id.desc())
        .limit(1)
    ).first()
    return (row[0], row[1]) if row else None


def situation_revision(session: Session, incident_id: int) -> str:
    """상황 버전(동시 수정 방지 토큰, 명세 2.4). 지금은 최신 정정 id — 방식은 Q-05 · Q-06."""
    correction = latest_correction(session, incident_id)
    return f"rev-{correction[0] if correction else 0}"


def latest_correction(session: Session, incident_id: int) -> tuple[int, datetime] | None:
    """(최신 상황 정정 id, 저장 시각)."""
    row = session.execute(
        text(
            "SELECT id, created_at FROM situation_corrections WHERE incident_id = :i"
            " ORDER BY id DESC LIMIT 1"
        ),
        {"i": incident_id},
    ).first()
    return (row[0], row[1]) if row else None
