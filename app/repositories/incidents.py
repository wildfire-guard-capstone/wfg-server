"""산불 · 화선 · 계정 · 직전 제안서 읽기 · 쓰기(DB 설계 2.1 · 2.2 · 2.4)."""

from datetime import datetime
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import FirePerimeter, Incident, Proposal, User
from app.db.models._types import SRID
from app.db.models.enums import UserRole


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
    situation: dict[str, Any] | None = None,
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
        situation=situation or {},
        reported_by=reported_by,
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


def get_incident(session: Session, incident_id: int) -> Incident:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise ValueError(f"산불 {incident_id}이 없습니다")
    return incident


def current_perimeter(session: Session, incident_id: int) -> FirePerimeter:
    """현재 화선 = 정정되지 않은 것 중 버전이 가장 큰 행(없으면 v0 발화점)."""
    perimeter = session.scalar(
        select(FirePerimeter)
        .where(FirePerimeter.incident_id == incident_id, FirePerimeter.corrected_at.is_(None))
        .order_by(FirePerimeter.version_no.desc())
        .limit(1)
    )
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
