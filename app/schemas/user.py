"""계정 DTO: User · AdminUser · AuthToken(명세 4절)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import User
from app.schemas.common import Id, In

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
