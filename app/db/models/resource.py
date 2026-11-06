"""진화자원 — 전산 관리자가 관리하는 기지와 헬기·차량 (DB 설계 2.6)."""

from typing import Any

from geoalchemy2 import WKBElement
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import geometry, jsonb_object, pg_enum
from app.db.models.enums import ResourceKind, ResourceStatus


class ResourceBase(Base):
    """헬기·차량이 소속된 기지 1곳. 관할 / 응원은 ``coverage_area_ids``로 판정."""

    __tablename__ = "resource_bases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    org_type: Mapped[str] = mapped_column(String(20))
    # Unknown for some leased-helicopter bases.
    geom: Mapped[WKBElement | None] = geometry("POINT", nullable=True)
    coverage_area_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default=text("'{}'")
    )


class Resource(Base):
    """헬기·차량 1대. 투입되면 ``deployed_incident_id``로 산불에 연결된다(A안, 11/6)."""

    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint(
            "(status = 'DEPLOYED') = (deployed_incident_id IS NOT NULL)",
            name="deployed_has_incident",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[ResourceKind] = mapped_column(pg_enum(ResourceKind, "resource_kind"))
    name: Mapped[str] = mapped_column(String(50), unique=True)
    base_id: Mapped[int] = mapped_column(ForeignKey("resource_bases.id"))
    status: Mapped[ResourceStatus] = mapped_column(
        pg_enum(ResourceStatus, "resource_status"), server_default=ResourceStatus.AVAILABLE.name
    )
    deployed_incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    attrs: Mapped[dict[str, Any]] = jsonb_object()
