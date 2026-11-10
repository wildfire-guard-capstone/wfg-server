URL = "/api/v1/event-logs"


def test_commander_reads_incident_logs_newest_first(client, login, reported):
    cmd = login("t_cmd", "COMMANDER")
    r = client.post(
        f"/api/v1/incidents/{reported['id']}/predictions",
        json={"expected_perimeter_id": reported["current_perimeter_id"]},
        headers=cmd,
    )
    assert r.status_code == 202
    logs = client.get(URL, params={"incident_id": reported["id"]}, headers=cmd)
    assert logs.headers["cache-control"] == "no-store"
    actions = [e["action"] for e in logs.json()["data"]["items"]]
    # 예측 작업이 제안서까지 저장한다(최신 먼저)
    assert actions[:3] == ["PROPOSAL_CREATED", "PREDICTION_CREATED", "INCIDENT_REPORTED"]
    only = client.get(
        URL, params={"incident_id": reported["id"], "action": "INCIDENT_REPORTED"}, headers=cmd
    ).json()["data"]["items"]
    assert [e["action"] for e in only] == ["INCIDENT_REPORTED"]
    assert client.get(URL, headers=cmd).status_code == 422


def test_admin_sees_account_logs_and_viewer_is_blocked(client, login):
    admin = login("t_admin", "ADMIN")
    items = client.get(URL, params={"action": "LOGIN", "limit": 100}, headers=admin).json()["data"]
    assert items["items"] and all(e["incident_id"] is None for e in items["items"])
    assert client.get(URL, headers=login("t_view", "VIEWER")).status_code == 403
