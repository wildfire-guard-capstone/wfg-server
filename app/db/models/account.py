"""계정 묶음 — 사용자와 이벤트 로그 (DB 설계 2.1)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._types import created_at, pg_enum
from app.db.models.enums import UserRole


class User(Base):
    """관리자가 발급하는 계정 1개. 회수해도 행은 남기고 ``deleted_at``만 기록."""

    __tablename__ = "users"
    __table_args__ = (
        # login_id is unique ignoring case.
        Index("uq_users_login_id_lower", text("lower(login_id)"), unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login_id: Mapped[str] = mapped_column(String(50))
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(50))
    organization: Mapped[str] = mapped_column(String(100))
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"))
    # 관할 시군구. NULL only for 전산 관리자 (all areas).
    admin_area_id: Mapped[int | None] = mapped_column(ForeignKey("admin_areas.id"))
    # Locked when >= 5.
    failed_login_count: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventLog(Base):
    """누가 · 언제 · 어느 산불에서 · 무엇을 했는지 1건. 추가만 한다."""

    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = created_at()
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"))
    action: Mapped[str] = mapped_column(String(50))
    # No personal data here.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
