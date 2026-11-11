"""USR-03 변경 · USR-04 회수 · USR-05 비밀번호 재발급 · USR-06 잠금 해제."""

from sqlalchemy import text

from tests.integration.conftest import PASSWORD

URL = "/api/v1/admin/users"
LOGIN = "/api/v1/auth/login"


def post_login(client, login_id, password=PASSWORD, role="COMMANDER"):
    return client.post(LOGIN, json={"login_id": login_id, "password": password, "role": role})


def only_test_commanders(db, uiseong):
    """이 DB에 원래 있던 의성군 통합지휘권자는 이 시험 동안 없는 것으로(끝나면 되돌아감)."""
    db.execute(
        text(
            "UPDATE users SET deleted_at = now() WHERE role = 'COMMANDER'"
            " AND admin_area_id = :a AND deleted_at IS NULL AND login_id NOT LIKE 't\\_%'"
        ),
        {"a": uiseong},
    )


def test_update_changes_only_sent_fields_and_logs(client, login, make_user, db):
    admin = login("t_admin", "ADMIN")
    user = make_user("t_view", "VIEWER")
    r = client.patch(f"{URL}/{user.id}", json={"organization": "의성군 산림녹지과"}, headers=admin)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()["data"]
    assert data["organization"] == "의성군 산림녹지과" and data["name"] == user.name
    assert data["role"] == "VIEWER" and data["deleted_at"] is None
    details = db.scalar(
        text("SELECT details FROM event_logs WHERE action = 'USER_UPDATED' ORDER BY id DESC")
    )
    assert details["target_user_id"] == user.id and details["changed"] == ["organization"]
    # 같은 값이면 바뀐 것이 없어 로그도 없다
    client.patch(f"{URL}/{user.id}", json={"organization": "의성군 산림녹지과"}, headers=admin)
    count = db.scalar(text("SELECT count(*) FROM event_logs WHERE action = 'USER_UPDATED'"))
    assert count == 1


def test_update_validates_input_and_jurisdiction(client, login, make_user):
    admin = login("t_admin", "ADMIN")
    user = make_user("t_view", "VIEWER")
    target = f"{URL}/{user.id}"
    assert client.patch(target, json={}, headers=admin).status_code == 422
    assert client.patch(target, json={"name": None}, headers=admin).status_code == 422
    assert client.patch(target, json={"login_id": "x"}, headers=admin).status_code == 422
    # 전산 관리자로 바꾸려면 관할도 null로 같이 보낸다
    r = client.patch(target, json={"role": "ADMIN"}, headers=admin)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_JURISDICTION"
    ok = client.patch(target, json={"role": "ADMIN", "admin_area_id": None}, headers=admin)
    assert ok.status_code == 200 and ok.json()["data"]["admin_area_id"] is None
    # 관할 없는 계정을 보고자로 바꿀 수는 없다
    r = client.patch(target, json={"role": "REPORTER"}, headers=admin)
    assert r.json()["error"]["code"] == "INVALID_JURISDICTION"
    assert client.patch(f"{URL}/999999999", json={"name": "x"}, headers=admin).status_code == 404


def test_last_commander_cannot_leave_or_be_revoked(client, login, make_user, db, uiseong):
    only_test_commanders(db, uiseong)
    admin = login("t_admin", "ADMIN")
    first = make_user("t_cmd1", "COMMANDER")
    r = client.patch(f"{URL}/{first.id}", json={"role": "VIEWER"}, headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LAST_COMMANDER"
    assert client.delete(f"{URL}/{first.id}", headers=admin).json()["error"]["code"] == (
        "LAST_COMMANDER"
    )
    # 이름 · 소속만 바꾸는 것은 된다
    assert (
        client.patch(f"{URL}/{first.id}", json={"name": "새 이름"}, headers=admin).status_code
        == 200
    )
    make_user("t_cmd2", "COMMANDER")  # 한 명 더 있으면 뺄 수 있다
    r = client.patch(f"{URL}/{first.id}", json={"role": "VIEWER"}, headers=admin)
    assert r.status_code == 200 and r.json()["data"]["role"] == "VIEWER"


def test_revoke_keeps_the_row_and_blocks_the_account(client, login, make_user, db):
    admin = login("t_admin", "ADMIN")
    view = login("t_view", "VIEWER")
    user_id = db.scalar(text("SELECT id FROM users WHERE login_id = 't_view'"))
    assert client.delete(f"{URL}/{user_id}", headers=admin).status_code == 204
    assert db.scalar(text("SELECT deleted_at FROM users WHERE id = :i"), {"i": user_id}) is not None
    # 이미 받은 토큰도 다음 요청에서 거절한다
    me = client.get("/api/v1/auth/me", headers=view)
    assert me.status_code == 403 and me.json()["error"]["code"] == "ACCOUNT_UNAVAILABLE"
    assert post_login(client, "t_view", role="VIEWER").status_code == 403
    assert client.delete(f"{URL}/{user_id}", headers=admin).status_code == 204  # 다시 해도 같다
    logs = db.scalar(text("SELECT count(*) FROM event_logs WHERE action = 'USER_REVOKED'"))
    assert logs == 1
    # 회수한 계정은 바꾸거나 다시 살리지 않는다
    for call in (
        client.patch(f"{URL}/{user_id}", json={"name": "x"}, headers=admin),
        client.post(f"{URL}/{user_id}/password-reset", headers=admin),
        client.post(f"{URL}/{user_id}/unlock", headers=admin),
    ):
        assert call.status_code == 409 and call.json()["error"]["code"] == "ACCOUNT_REVOKED"
    listed = client.get(URL, params={"limit": 100}, headers=admin).json()["data"]["items"]
    assert "t_view" not in [u["login_id"] for u in listed]


def test_admin_cannot_revoke_or_demote_itself(client, login, db):
    admin = login("t_admin", "ADMIN")
    me = client.get("/api/v1/auth/me", headers=admin).json()["data"]["id"]
    r = client.delete(f"{URL}/{me}", headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SELF_MODIFICATION"
    area = db.scalar(text("SELECT id FROM admin_areas WHERE level = 'SIGUNGU' LIMIT 1"))
    r = client.patch(f"{URL}/{me}", json={"role": "VIEWER", "admin_area_id": area}, headers=admin)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SELF_MODIFICATION"
    assert client.patch(f"{URL}/{me}", json={"name": "관리자"}, headers=admin).status_code == 200


def test_password_reset_replaces_the_password(client, login, make_user, db):
    admin = login("t_admin", "ADMIN")
    user = make_user("t_cmd", "COMMANDER")
    r = client.post(f"{URL}/{user.id}/password-reset", headers=admin)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()["data"]
    assert data["user_id"] == str(user.id) and len(data["temporary_password"]) >= 12
    assert post_login(client, "t_cmd").status_code == 401  # 옛 비밀번호
    assert post_login(client, "t_cmd", password=data["temporary_password"]).status_code == 200
    details = db.scalar(text("SELECT details FROM event_logs WHERE action = 'USER_PASSWORD_RESET'"))
    assert details == {"target_user_id": user.id}  # 비밀번호는 로그에 없다


def test_unlock_resets_failed_logins(client, login, make_user):
    admin = login("t_admin", "ADMIN")
    user = make_user("t_cmd", "COMMANDER")
    for _ in range(5):
        assert post_login(client, "t_cmd", password="틀린-비밀번호").status_code == 401
    locked = post_login(client, "t_cmd")
    assert locked.status_code == 403 and locked.json()["error"]["code"] == "ACCOUNT_UNAVAILABLE"
    r = client.post(f"{URL}/{user.id}/unlock", headers=admin)
    assert r.status_code == 200 and r.json()["data"]["failed_login_count"] == 0
    assert post_login(client, "t_cmd").status_code == 200


def test_only_admin_manages_accounts(client, login, make_user):
    cmd = login("t_cmd", "COMMANDER")
    user = make_user("t_view", "VIEWER")
    for call in (
        client.patch(f"{URL}/{user.id}", json={"name": "x"}, headers=cmd),
        client.delete(f"{URL}/{user.id}", headers=cmd),
        client.post(f"{URL}/{user.id}/password-reset", headers=cmd),
        client.post(f"{URL}/{user.id}/unlock", headers=cmd),
    ):
        assert call.status_code == 403 and call.json()["error"]["code"] == "FORBIDDEN_ROLE"
