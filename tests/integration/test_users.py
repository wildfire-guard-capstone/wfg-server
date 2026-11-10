URL = "/api/v1/admin/users"


def test_admin_lists_and_issues_accounts(client, login, uiseong):
    admin = login("t_admin", "ADMIN")
    body = {
        "login_id": "t_new",
        "name": "새 지휘권자",
        "organization": "의성군",
        "role": "COMMANDER",
        "admin_area_id": str(uiseong),
    }
    r = client.post(URL, json=body, headers=admin)
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    data = r.json()["data"]
    assert data["user"]["admin_area_id"] == str(uiseong) and data["user"]["failed_login_count"] == 0
    temp = data["temporary_password"]
    ok = client.post(
        "/api/v1/auth/login", json={"login_id": "t_new", "password": temp, "role": "COMMANDER"}
    )
    assert ok.status_code == 200
    page = client.get(URL, params={"role": "COMMANDER", "limit": 100}, headers=admin).json()["data"]
    assert "t_new" in [u["login_id"] for u in page["items"]]


def test_issue_rejects_duplicates_and_bad_jurisdiction(client, login, uiseong):
    admin = login("t_admin2", "ADMIN")
    dup = {
        "login_id": "T_ADMIN2",
        "name": "a",
        "organization": "b",
        "role": "ADMIN",
        "admin_area_id": None,
    }
    assert client.post(URL, json=dup, headers=admin).json()["error"]["code"] == "DUPLICATE_LOGIN_ID"
    bad = dict(dup, login_id="t_x", role="VIEWER")
    r = client.post(URL, json=bad, headers=admin)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_JURISDICTION"


def test_paging_and_role_guard(client, login, make_user):
    admin = login("t_admin3", "ADMIN")
    for i in range(3):
        make_user(f"t_page{i}", "VIEWER")
    first = client.get(URL, params={"role": "VIEWER", "limit": 2}, headers=admin).json()["data"]
    assert len(first["items"]) == 2 and first["has_more"]
    nxt = client.get(
        URL, params={"role": "VIEWER", "limit": 2, "cursor": first["next_cursor"]}, headers=admin
    ).json()["data"]
    assert {u["id"] for u in nxt["items"]}.isdisjoint({u["id"] for u in first["items"]})
    viewer = login("t_view", "VIEWER")
    assert client.get(URL, headers=viewer).json()["error"]["code"] == "FORBIDDEN_ROLE"
