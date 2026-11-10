"""PROP-01 ~ 04 제안서 조회 + INC-03 running_operation_id (가짜 작성기로 만든 제안서)."""

from app.services import operations
from app.services.proposal_compare import CODES
from tests.integration.conftest import square
from tests.integration.test_predictions import run

URL = "/api/v1/incidents"


def blocks(item):
    return [b for s in item["sections"] for b in s["blocks"]]


def test_proposal_versions_view_and_diff(client, login, reported, anpyeong):
    cmd, rep = login("t_cmd_p", "COMMANDER"), login("t_rep_p", "REPORTER")
    inc = f"{URL}/{reported['id']}"
    assert client.get(f"{inc}/proposals/latest", headers=cmd).json()["data"] is None

    first = run(client, reported, cmd)["result"]["proposal_id"]  # v0.1
    second = run(client, reported, cmd)["result"]["proposal_id"]  # v0.2 같은 화선
    new = {
        "geometry": square(*anpyeong),
        "observed_at": "2025-03-22T13:00:00+09:00",
        "expected_perimeter_id": reported["current_perimeter_id"],
    }
    r = client.post(f"{inc}/perimeters", json=new, headers=rep)
    assert r.status_code == 201, r.text
    detail = client.get(inc, headers=cmd).json()["data"]
    third = run(client, detail, cmd)["result"]["proposal_id"]  # v1.1 새 화선

    # PROP-01
    page = client.get(f"{inc}/proposals", headers=cmd).json()["data"]
    rows = page["items"]
    assert [(p["id"], p["version"]) for p in rows] == [
        (third, "v1.1"),
        (second, "v0.2"),
        (first, "v0.1"),
    ]
    assert rows[1]["changed_count"] == 0 and rows[2]["changed_count"] is None
    assert rows[0]["trigger"] == "PREDICTION" and rows[0]["judged_stage"] in (
        "INITIAL",
        "STAGE1",
        "STAGE2",
    )
    assert set(rows[0]["category_counts"]) == {"IMMEDIATE", "STANDBY", "CONSULT", "REQUEST"}
    one = client.get(f"{inc}/proposals", params={"limit": 1}, headers=cmd).json()["data"]
    assert one["has_more"] and one["items"][0]["changed_count"] == rows[0]["changed_count"]
    nxt = client.get(
        f"{inc}/proposals", params={"limit": 1, "cursor": one["next_cursor"]}, headers=cmd
    ).json()["data"]
    assert nxt["items"][0]["id"] == second

    # PROP-02 최신
    latest = client.get(f"{inc}/proposals/latest", headers=cmd).json()["data"]
    assert latest["id"] == third and latest["version"] == "v1.1"
    assert latest["base_at"] == "2025-03-22T13:00:00+09:00"
    assert [i["item_code"] for i in latest["items"]] == CODES
    for item in latest["items"]:
        if item["state"] == "NOT_TRIGGERED":
            assert item["sections"] == [] and item["change_type"] is None
            continue
        assert item["finding"] and item["change_type"] in ("CHANGED", "SAME", "NEW")
        for b in blocks(item):
            assert "quote_ok" not in b and "action" not in b and "writer" not in b
            if b["type"] == "paragraph":
                assert b["category"] in ("IMMEDIATE", "STANDBY", "CONSULT", "REQUEST")
            if b["type"] == "table":
                assert all(
                    isinstance(row, list) and len(row) == len(b["head"]) for row in b["rows"]
                )
        assert item["evidence"][0]["chunk_id"] == "TEST-0001"

    # PROP-03 지난 버전: 첫 버전은 비교 대상이 없어 「변경」 태그 없음
    old = client.get(f"{inc}/proposals/{first}", headers=cmd).json()["data"]
    assert old["version"] == "v0.1" and all(i["change_type"] is None for i in old["items"])
    assert client.get(f"{inc}/proposals/999999999", headers=cmd).status_code == 404

    # PROP-04
    d1 = client.get(f"{inc}/proposals/{first}/diff", headers=cmd).json()["data"]
    assert d1["previous_version"] is None and d1["counts"] is None and d1["items"] == []
    d2 = client.get(f"{inc}/proposals/{second}/diff", headers=cmd).json()["data"]
    assert d2["previous_version"] == "v0.1" and d2["counts"]["changed"] == 0
    assert d2["cause"].startswith("같은 화선에서 다시 예측")
    d3 = client.get(f"{inc}/proposals/{third}/diff", headers=cmd).json()["data"]
    assert d3["cause"].startswith("새 화선(v1, 관측 13:00)")
    assert d3["counts"]["changed"] == len(d3["items"]) == rows[0]["changed_count"]
    assert sum(d3["counts"].values()) == len(d3["items"]) + len(d3["wording_only_items"]) + len(
        d3["same_items"]
    )

    view = login("t_view_p", "VIEWER")
    assert client.get(f"{inc}/proposals/latest", headers=view).status_code == 403


def test_detail_shows_running_operation(client, login, reported):
    cmd = login("t_cmd_op", "COMMANDER")
    inc = f"{URL}/{reported['id']}"
    assert client.get(inc, headers=cmd).json()["data"]["running_operation_id"] is None
    op = operations.start("PREDICTION", int(reported["id"]), 0)
    try:
        assert client.get(inc, headers=cmd).json()["data"]["running_operation_id"] == op.id
    finally:
        operations.clear()
