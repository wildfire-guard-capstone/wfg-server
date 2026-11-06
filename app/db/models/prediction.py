"""예측 묶음 — 확산 예측 1회와 P1~P8 (DB 설계 2.3)."""

from datetime import datetime
from typing import Any

from geoalchemy2 import WKBElement
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import created_at, geometry


class Prediction(Base):
    """성공한 예측 1회. Pn의 시각 = ``base_at`` + n시간."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    perimeter_id: Mapped[int] = mapped_column(ForeignKey("fire_perimeters.id"))
    # observed_at of the starting perimeter, not the time the button was pressed.
    base_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # {"source", "issued_at", "location", "hours": [9 hourly rows]}
    weather: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class PredictionSlice(Base):
    """예측 1회의 누적 확산 범위 1개(P1~P8). P5 = 위험구역, P8 = 잠재 위험구역."""

    __tablename__ = "prediction_slices"
    __table_args__ = (
        UniqueConstraint("prediction_id", "hour"),
        CheckConstraint("hour BETWEEN 1 AND 8", name="hour_1_to_8"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id"))
    hour: Mapped[int] = mapped_column(SmallInteger)
    geom: Mapped[WKBElement] = geometry("MULTIPOLYGON")
