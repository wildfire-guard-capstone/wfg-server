"""계정 DTO: User · AdminUser · AuthToken(명세 4절)."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.db.models import User
from app.schemas.common import DateTimeOut, Id, In, need_one, no_nulls

RoleName = Literal["COMMANDER", "VIEWER", "REPORTER", "ADMIN"]


class UserOut(BaseModel):
    id: Id
    login_id: str
    name: str
    organization: str
    role: RoleName
    admin_area_id: Id | None

    @classmethod
    def of(cls, u: User) -> "UserOut":
        return cls(
            id=u.id,
            login_id=u.login_id,
            name=u.name,
            organization=u.organization,
            role=u.role.name,
            admin_area_id=u.admin_area_id,
        )


class AuthToken(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut


class LoginIn(In):
    login_id: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1)
    role: RoleName


class AdminUserOut(UserOut):
    failed_login_count: int
    created_at: DateTimeOut
    updated_at: DateTimeOut
    deleted_at: DateTimeOut | None

    @classmethod
    def of(cls, u: User) -> "AdminUserOut":
        return cls(
            **UserOut.of(u).model_dump(),
            failed_login_count=u.failed_login_count,
            created_at=u.created_at,
            updated_at=u.updated_at,
            deleted_at=u.deleted_at,
        )


class UserCreateIn(In):
    login_id: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=50)
    organization: str = Field(min_length=1, max_length=100)
    role: RoleName
    admin_area_id: int | None  # 키는 꼭 보내고, 전산 관리자만 null


class UserCreated(BaseModel):
    user: AdminUserOut
    temporary_password: str


class UserUpdateIn(In):
    """USR-03. 보낸 것만 바꾼다(1개 이상). admin_area_id만 null을 받는다(전산 관리자)."""

    name: str | None = Field(None, min_length=1, max_length=50)
    organization: str | None = Field(None, min_length=1, max_length=100)
    role: RoleName | None = None
    admin_area_id: int | None = None

    @model_validator(mode="after")
    def _check(self) -> "UserUpdateIn":
        need_one(self)
        no_nulls(self, "name", "organization", "role")
        return self


class PasswordReset(BaseModel):
    user_id: Id
    temporary_password: str
