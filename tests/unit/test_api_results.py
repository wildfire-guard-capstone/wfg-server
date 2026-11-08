import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.security import hash_password, issue_token, read_token
from app.main import app

SECRET = "s" * 32
PASSWORD = "correct-horse-9"
USERS = {"cmd01": "COMMANDER", "view01": "VIEWER", "rep01": "REPORTER", "admin01": "ADMIN"}


@pytest.fixture
def accounts_file(tmp_path):
    path = tmp_path / "accounts.json"
    rows = [
        {
            "login_id": login,
            # 테스트는 반복 횟수를 줄여 빠르게(저장된 횟수로 검증하므로 그대로 동작)
            "password_hash": hash_password(PASSWORD, iterations=1_000),
            "name": login,
            "organization": "의성군",
            "role": role,
            "disabled": False,
        }
        for login, role in USERS.items()
    ]
    path.write_text(json.dumps({"accounts": rows}), encoding="utf-8")
    return path


@pytest.fixture
def client(bundle_dir, accounts_file):
    settings = Settings(
        _env_file=None, results_dir=bundle_dir, accounts_file=accounts_file, auth_secret=SECRET
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, login_id, password=PASSWORD):
    return client.post("/api/auth/login", json={"login_id": login_id, "password": password})


def _headers(client, login_id):
    token = _login(client, login_id).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ── 로그인 ────────────────────────────────────────────────────────────────


def test_login_returns_token_and_user(client):
    r = _login(client, "CMD01")  # 아이디는 대소문자 무시
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == {
        "login_id": "cmd01",
        "name": "cmd01",
        "organization": "의성군",
        "role": "통합지휘권자",
    }
    assert read_token(body["access_token"], SECRET).sub == "cmd01"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["role"] == "통합지휘권자"


def test_wrong_password_then_lock_after_five(client):
    for _ in range(5):
        assert _login(client, "view01", "wrong").status_code == 401
    assert _login(client, "view01").status_code == 423  # 맞는 비밀번호여도 잠김
    assert _login(client, "cmd01").status_code == 200  # 다른 계정은 영향 없음


def test_unknown_user_is_401(client):
    assert _login(client, "nobody").status_code == 401


def test_missing_bad_expired_tokens_are_401(client):
    assert client.get("/api/incidents").status_code == 401
    bad = {"Authorization": "Bearer abc.def"}
    assert client.get("/api/incidents", headers=bad).status_code == 401
    expired = issue_token("cmd01", "COMMANDER", SECRET, 60, now=1_000)
    r = client.get("/api/incidents", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
    forged = issue_token("cmd01", "COMMANDER", "x" * 32, 600)
    r = client.get("/api/incidents", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_removed_account_token_stops_working(client, accounts_file):
    headers = _headers(client, "cmd01")
    data = json.loads(accounts_file.read_text(encoding="utf-8"))
    data["accounts"] = [a for a in data["accounts"] if a["login_id"] != "cmd01"]
    accounts_file.write_text(json.dumps(data), encoding="utf-8")
    assert client.get("/api/incidents", headers=headers).status_code == 401


def test_not_configured_is_503(bundle_dir):
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    try:
        c = TestClient(app)
        assert _login(c, "cmd01").status_code == 503
        assert c.get("/api/incidents").status_code in (401, 503)
    finally:
        app.dependency_overrides.clear()


def test_short_secret_is_503(bundle_dir, accounts_file):
    settings = Settings(_env_file=None, accounts_file=accounts_file, auth_secret="short")
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        assert _login(TestClient(app), "cmd01").status_code == 503
    finally:
        app.dependency_overrides.clear()


# ── 권한 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/api/incidents", {"cmd01", "view01", "rep01"}),
        ("/api/incidents/1/perimeters", {"cmd01", "view01", "rep01"}),
        ("/api/incidents/1/predictions", {"cmd01", "view01"}),
        ("/api/incidents/1/predictions/1", {"cmd01", "view01"}),
        ("/api/incidents/1/proposals", {"cmd01"}),
        ("/api/incidents/1/proposals/latest", {"cmd01"}),
    ],
)
def test_role_permissions(client, path, allowed):
    for login in USERS:
        r = client.get(path, headers=_headers(client, login))
        assert r.status_code == (200 if login in allowed else 403), (login, path)


# ── 조회 ──────────────────────────────────────────────────────────────────


def test_incident_and_perimeters(client):
    h = _headers(client, "cmd01")
    [inc] = client.get("/api/incidents", headers=h).json()
    assert inc["id"] == 1 and inc["current_stage"] == "확산대응 2단계"
    assert "ignition_point" not in inc  # 목록은 요약만
    detail = client.get("/api/incidents/1", headers=h).json()
    assert detail["ignition_point"]["type"] == "Point"
    perims = client.get("/api/incidents/1/perimeters", headers=h).json()
    assert [p["version_no"] for p in perims] == [0, 1, 2]
    light = client.get("/api/incidents/1/perimeters?geometry=false", headers=h).json()
    assert light[1]["geometry"] == {"type": "MultiPolygon"}


def test_predictions(client):
    h = _headers(client, "view01")
    rows = client.get("/api/incidents/1/predictions", headers=h).json()
    assert [(r["id"], r["perimeter_version"]) for r in rows] == [(1, 1), (2, 2)]
    assert "slices" not in rows[0]
    pred = client.get("/api/incidents/1/predictions/2", headers=h).json()
    assert len(pred["slices"]) == 8 and pred["slices"][0]["hour"] == 1
    assert pred["calibration"] == {"method": "rate", "adj": 2.751, "basis": "성장 속도 유지"}
    assert pred["weather"][0] == {"hour": 0, "wind_ms": 3.0, "wind_deg": 270.0}


def test_proposals_latest_and_by_version(client):
    h = _headers(client, "cmd01")
    rows = client.get("/api/incidents/1/proposals", headers=h).json()
    assert [r["version"] for r in rows] == ["v1.1", "v2.1"]
    assert rows[0]["item_states"] == {"S1": "ok", "E2": "partial"}
    assert "items" not in rows[0]
    latest = client.get("/api/incidents/1/proposals/latest", headers=h).json()
    assert latest["version"] == "v2.1" and latest["reader"] == "경상북도지사"
    item = client.get("/api/incidents/1/proposals/v1.1", headers=h).json()["items"][0]
    block = item["sections"][0]["blocks"][0]
    assert block["text"].endswith("[p.79].") and block["evidence"] == ["SM-p079-s01"]
    assert item["evidence"][0]["text"] == "근거 원문"


@pytest.mark.parametrize(
    "path",
    [
        "/api/incidents/9",
        "/api/incidents/9/perimeters",
        "/api/incidents/9/proposals",
        "/api/incidents/1/predictions/99",
        "/api/incidents/1/proposals/v9.9",
        "/api/incidents/1/proposals/..%2F..%2Fmanifest",
    ],
)
def test_not_found(client, path):
    assert client.get(path, headers=_headers(client, "cmd01")).status_code == 404
