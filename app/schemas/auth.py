from datetime import datetime

from pydantic import BaseModel

from app.db.models.enums import UserRole


class LoginRequest(BaseModel):
    login_id: str
    password: str


class UserOut(BaseModel):
    login_id: str
    name: str
    organization: str
    role: UserRole


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut
