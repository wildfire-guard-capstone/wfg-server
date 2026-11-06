"""산불·보고 묶음 — 산불 사건과 실측 화선 (DB 설계 2.2)."""

from datetime import datetime
from typing import Any

from geoalchemy2 import WKBElement
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import created_at, geometry, jsonb_object, pg_enum
from app.db.models.enums import IncidentStatus, ResponseStage


class Incident(Base):
    """산불 하나. 현재 상황(``situation``)과 현재 대응단계를 담는다."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ignition_point: Mapped[WKBElement] = geometry("POINT")
    address: Mapped[str] = mapped_column(String(200))
    # 읍면동 containing the ignition point, computed on save.
    admin_area_id: Mapped[int] = mapped_column(ForeignKey("admin_areas.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    report_text: Mapped[str | None] = mapped_column(Text)
    reporter_org: Mapped[str | None] = mapped_column(String(100))
    reporter_name: Mapped[str] = mapped_column(String(50))
    reporter_phone: Mapped[str] = mapped_column(String(20))
    status: Mapped[IncidentStatus] = mapped_column(
        pg_enum(IncidentStatus, "incident_status"),
        server_default=IncidentStatus.IN_PROGRESS.name,
    )
    # Judged by the rule engine on every perimeter report/correction, never entered by hand.
    current_stage: Mapped[ResponseStage] = mapped_column(
        pg_enum(ResponseStage, "response_stage"), server_default=ResponseStage.INITIAL.name
    )
    # {"villages": {...}, "deployed": {...}, "disaster_sms_sent": bool}
    situation: Mapped[dict[str, Any]] = jsonb_object()
    reported_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class FirePerimeter(Base):
    """실측 화선 1건. v0 = 발화점(Point), v1 이상 = 연소 범위(MultiPolygon)."""

    __tablename__ = "fire_perimeters"
    __table_args__ = (
        UniqueConstraint("incident_id", "version_no"),
        CheckConstraint(
            "(version_no = 0 AND GeometryType(geom) = 'POINT')"
            " OR (version_no > 0 AND GeometryType(geom) = 'MULTIPOLYGON')",
            name="v0_point_else_multipolygon",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    geom: Mapped[WKBElement] = geometry()
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reported_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    # Set when the perimeter was redrawn; NULL = valid.
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
