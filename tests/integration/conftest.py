"""API 통합 테스트: 로컬 DB(바탕 데이터 적재됨)에서 돌리고, 테스트마다 모두 되돌린다.

    WFG_INTEGRATION=1 uv run pytest tests/integration

CI에는 DB가 없어 건너뛴다.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.enums import UserRole
from app.services.accounts import create_user

PASSWORD = "test-pw-1234"


@pytest.fixture(scope="session")
def engine():
    if os.environ.get("WFG_INTEGRATION") != "1":
        pytest.skip("통합 테스트는 WFG_INTEGRATION=1일 때만(로컬 DB 필요)")
    eng = create_engine(get_settings().database_url)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        pytest.skip("DB에 연결할 수 없음")
    return eng


@pytest.fixture
def db(engine):
    """바깥 트랜잭션 안에서만 쓰는 세션. 라우터의 commit은 savepoint까지만 반영된다."""
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield session
    session.close()
    outer.rollback()
    conn.close()


@pytest.fixture
def client(db):
    from app.db.session import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def uiseong(db) -> int:
    return db.scalar(text("SELECT id FROM admin_areas WHERE level = 'SIGUNGU' AND name = '의성군'"))


@pytest.fixture
def make_user(db, uiseong):
    def _make(login_id: str, role: str, area: int | None = -1):
        area_id = None if role == "ADMIN" else (uiseong if area == -1 else area)
        user = create_user(
            db,
            login_id=login_id,
            name=f"시험 {login_id}",
            organization="시험",
            role=UserRole[role],
            admin_area_id=area_id,
            password=PASSWORD,
        )
        db.flush()
        return user

    return _make


@pytest.fixture
def login(client, make_user):
    """계정을 만들고 로그인해 Authorization 헤더를 돌려준다."""

    def _login(login_id: str, role: str) -> dict[str, str]:
        make_user(login_id, role)
        r = client.post(
            "/api/v1/auth/login", json={"login_id": login_id, "password": PASSWORD, "role": role}
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    return _login
