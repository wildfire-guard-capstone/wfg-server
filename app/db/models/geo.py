"""지도·자원 묶음 — 바뀌지 않는 바탕 데이터 (DB 설계 2.6)."""

from datetime import date
from typing import Any

from geoalchemy2 import WKBElement
from sqlalchemy import (
    CHAR,
    BigInteger,
    Date,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import geometry, jsonb_object, pg_enum
from app.db.models.enums import AdminLevel, FacilityType


class AdminArea(Base):
    """시도·시군구·읍면동 1개. 코드는 법정동코드 10자리."""

    __tablename__ = "admin_areas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    level: Mapped[AdminLevel] = mapped_column(pg_enum(AdminLevel, "admin_level"))
    code: Mapped[str] = mapped_column(CHAR(10), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("admin_areas.id"))
    # Adjacent 시군구 ids, filled once at load time (시군구 rows only).
    neighbor_ids: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), nullable=False, server_default=text("'{}'")
    )
    geom: Mapped[WKBElement] = geometry("MULTIPOLYGON")


class Village(Base):
    """법정리 1개 — 위험구역 · 대피 판정의 단위."""

    __tablename__ = "villages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(CHAR(10), unique=True)
    admin_area_id: Mapped[int] = mapped_column(ForeignKey("admin_areas.id"))
    name: Mapped[str] = mapped_column(String(50))
    geom: Mapped[WKBElement] = geometry("MULTIPOLYGON")
    label_point: Mapped[WKBElement] = geometry("POINT")
    population: Mapped[int] = mapped_column(Integer)
    population_month: Mapped[date] = mapped_column(Date)
    pop65_est: Mapped[int | None] = mapped_column(Integer)
    pop0_14_est: Mapped[int | None] = mapped_column(Integer)
    houses_est: Mapped[int | None] = mapped_column(Integer)


class AdminVillage(Base):
    """행정리 1개(조례 「이장 정수」 별표) — 법정리 하나를 이장 여럿이 나눠 맡는 단위.

    법정리(villages)는 그대로 두고 그 아래에 둔다. 경계는 공개 자료에 없어, 행정리 회관에서 시작하는
    K-평균으로 법정리 안 주택을 나눈 마을 구역(geom)을 회관을 모두 찾은 법정리에만 둔다.
    법정리가 나뉘지 않으면 행정리 = 법정리라 회관 · 구역이 없다.
    """

    __tablename__ = "admin_villages"
    __table_args__ = (UniqueConstraint("village_id", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    village_id: Mapped[int] = mapped_column(ForeignKey("villages.id"))
    name: Mapped[str] = mapped_column(String(50))
    seq: Mapped[int] = mapped_column(SmallInteger)  # 별표 순서(1리 · 2리 …)
    hall_name: Mapped[str | None] = mapped_column(String(100))
    hall_point: Mapped[WKBElement | None] = geometry("POINT", nullable=True)
    hall_source: Mapped[str | None] = mapped_column(String(200))
    geom: Mapped[WKBElement | None] = geometry("MULTIPOLYGON", nullable=True)  # 마을 구역
    houses_est: Mapped[int | None] = mapped_column(Integer)  # 구역 안 주택 동수
    attrs: Mapped[dict[str, Any]] = jsonb_object()  # 구역 만든 방법 · 회관 대조 메모


class Facility(Base):
    """제안에 이름이 나오는 시설 1개. 종류별 값은 ``attrs``."""

    __tablename__ = "facilities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[FacilityType] = mapped_column(pg_enum(FacilityType, "facility_type"))
    subtype: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100))
    capacity: Mapped[int | None] = mapped_column(Integer)
    capacity_unit: Mapped[str | None] = mapped_column(String(10))
    address: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(20))
    # NULL for facilities outside the 관할 (e.g. neighbouring fire stations).
    village_id: Mapped[int | None] = mapped_column(ForeignKey("villages.id"))
    sigungu_id: Mapped[int] = mapped_column(ForeignKey("admin_areas.id"))
    geom: Mapped[WKBElement] = geometry()
    attrs: Mapped[dict[str, Any]] = jsonb_object()


class MapFeature(Base):
    """도로 · 송전 · 담수지 · 주택 점 등 시설이 아닌 지도 요소 1개."""

    __tablename__ = "map_features"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # String, not ENUM: kinds keep growing (road, track, power_line, house, ...).
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str | None] = mapped_column(String(100))
    geom: Mapped[WKBElement] = geometry()
    attrs: Mapped[dict[str, Any]] = jsonb_object()
