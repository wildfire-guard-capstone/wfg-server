from sqlalchemy import text

from tests.integration.conftest import PASSWORD

URL = "/api/v1/auth"


def post_login(client, login_id, password=PASSWORD, role="COMMANDER"):
    return client.post(
        f"{URL}/login", json={"login_id": login_id, "password": password, "role": role}
    )


def test_login_me_logout(client, make_user, db):
    make_user("t_cmd", "COMMANDER")
    r = post_login(client, "T_CMD")  # 아이디는 대소문자 무시
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["token_type"] == "bearer" and data["expires_in"] == 1800
    assert data["user"]["login_id"] == "t_cmd" and data["user"]["role"] == "COMMANDER"
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    me = client.get(f"{URL}/me", headers=headers).json()["data"]
    assert me["id"] == data["user"]["id"]
    assert client.post(f"{URL}/logout", headers=headers).status_code == 204
    actions = db.scalars(
        text("SELECT action FROM event_logs WHERE user_id = :u ORDER BY id"), {"u": int(me["id"])}
    ).all()
    assert actions == ["LOGIN", "LOGOUT"]


def test_wrong_password_counts_and_locks_after_five(client, make_user):
    make_user("t_lock", "VIEWER")
    for _ in range(5):
        r = post_login(client, "t_lock", "wrong", "VIEWER")
        assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = post_login(client, "t_lock", PASSWORD, "VIEWER")
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_UNAVAILABLE"


def test_role_mismatch_and_unknown_user(client, make_user):
    make_user("t_rep", "REPORTER")
    r = post_login(client, "t_rep", PASSWORD, "COMMANDER")
    assert r.status_code == 403 and r.json()["error"]["code"] == "ROLE_MISMATCH"
    assert post_login(client, "nobody_xyz").status_code == 401


def test_me_needs_valid_token(client):
    assert client.get(f"{URL}/me").json()["error"]["code"] == "UNAUTHORIZED"
    r = client.get(f"{URL}/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"
