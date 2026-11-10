URL = "/api/v1/map/layers"
# 의성 안평면 둘레(경도 · 위도)
BBOX = "128.55,36.33,128.65,36.42"


def test_layers_report_what_is_loaded(client, login):
    cmd = login("t_cmd", "COMMANDER")
    layers = client.get(URL, headers=cmd).json()["data"]
    by_id = {x["id"]: x for x in layers}
    assert list(by_id) == [
        "admin_areas", "villages", "facilities", "road", "track",
        "power_line", "power_tower", "substation", "water", "house",
    ]  # fmt: skip
    assert all(x["available"] for x in layers)
    assert by_id["road"]["attribution"] == "© OpenStreetMap contributors"


def test_features_in_bbox_with_paging(client, login):
    rep = login("t_rep", "REPORTER")
    first = client.get(f"{URL}/villages/features", params={"bbox": BBOX, "limit": 2}, headers=rep)
    data = first.json()["data"]
    assert data["type"] == "FeatureCollection" and len(data["features"]) == 2 and data["has_more"]
    f = data["features"][0]
    assert f["geometry"]["type"] == "MultiPolygon" and isinstance(f["id"], str)
    assert {"name", "population", "label_point", "admin_area_id"} <= set(f["properties"])
    nxt = client.get(
        f"{URL}/villages/features",
        params={"bbox": BBOX, "limit": 2, "cursor": data["next_cursor"]},
        headers=rep,
    ).json()["data"]
    assert {x["id"] for x in nxt["features"]}.isdisjoint({x["id"] for x in data["features"]})

    fac = client.get(f"{URL}/facilities/features", params={"bbox": BBOX}, headers=rep).json()[
        "data"
    ]
    assert fac["features"] and "attrs" in fac["features"][0]["properties"]
    road = client.get(f"{URL}/road/features", params={"bbox": BBOX}, headers=rep).json()["data"]
    assert road["features"][0]["properties"]["kind"] == "road"


def test_bad_bbox_and_unknown_layer(client, login):
    view = login("t_view", "VIEWER")
    r = client.get(f"{URL}/villages/features", params={"bbox": "1,2,3"}, headers=view)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_BBOX"
    r = client.get(
        f"{URL}/villages/features", params={"bbox": "128.7,36.3,128.6,36.4"}, headers=view
    )
    assert r.json()["error"]["code"] == "INVALID_BBOX"
    r = client.get(f"{URL}/nope/features", params={"bbox": BBOX}, headers=view)
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNKNOWN_LAYER"
    admin = login("t_admin", "ADMIN")
    assert client.get(URL, headers=admin).status_code == 403
