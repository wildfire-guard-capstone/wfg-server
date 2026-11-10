URL = "/api/v1"


def test_resources_need_incident_for_commander(client, login, reported):
    cmd = login("t_cmd", "COMMANDER")
    r = client.get(f"{URL}/resources", headers=cmd)
    assert r.status_code == 422 and r.json()["error"]["fields"][0]["field"] == "incident_id"
    page = client.get(
        f"{URL}/resources",
        params={"incident_id": reported["id"], "kind": "HELICOPTER", "limit": 5},
        headers=cmd,
    ).json()["data"]
    assert len(page["items"]) == 5 and page["has_more"]
    item = page["items"][0]
    assert item["kind"] == "HELICOPTER" and item["deployed_incident_id"] is None
    assert isinstance(item["base_id"], str)


def test_admin_lists_without_incident(client, login):
    admin = login("t_admin", "ADMIN")
    page = client.get(f"{URL}/resources", params={"limit": 100}, headers=admin).json()["data"]
    assert len(page["items"]) == 100 and page["has_more"]
    assert client.get(f"{URL}/resources", headers=login("t_rep", "REPORTER")).status_code == 403


def test_summary_counts_and_incident_numbers(client, login, reported):
    view = login("t_view", "VIEWER")
    s = client.get(
        f"{URL}/resources/summary", params={"incident_id": reported["id"]}, headers=view
    ).json()["data"]
    assert s["bases"] and all(
        b["total"] == b["available"] + b["deployed"] + b["maintenance"] for b in s["bases"]
    )
    assert sum(b["total"] for b in s["bases"]) >= 162
    assert s["incident_deployed"] is None  # 상황 보고 전
    assert s["incident_linked"] == {"HELICOPTER": 0, "VEHICLE": 0}
    admin = login("t_admin2", "ADMIN")
    plain = client.get(f"{URL}/resources/summary", headers=admin).json()["data"]
    assert plain["incident_deployed"] is None and plain["incident_linked"] is None


def test_bases_distance_and_jurisdiction(client, login, reported):
    cmd = login("t_cmd2", "COMMANDER")
    page = client.get(
        f"{URL}/resource-bases", params={"incident_id": reported["id"], "limit": 100}, headers=cmd
    ).json()["data"]
    bases = page["items"]
    assert len(bases) >= 50
    located = [b for b in bases if b["geometry"]]
    assert all(b["distance_km"] is not None and b["jurisdiction"] for b in located)
    assert any(b["geometry"] is None and b["distance_km"] is None for b in bases)
    assert {"LOCAL", "SUPPORT"} <= {b["jurisdiction"] for b in bases}
