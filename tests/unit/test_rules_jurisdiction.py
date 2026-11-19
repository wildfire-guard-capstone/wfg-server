"""관할 기관 이름(11/19 평가 RE-R2): 관할 이름이 S2 · E2 · E3 · E5 입력에 들어가는지.

전에는 S6 · E6 요청 표에만 있어서 그 항목을 쓰는 LLM이 관할 기관을 몰랐다. 이름은 고정값이 아니라
산불 범위가 걸친 시군구에서 찾은 값이라, 산불 위치를 바꾸면 이름도 바뀌고 자료가 없으면 null이다.
"""

import dataclasses

from rules_example import full_inputs, place, village
from shapely.geometry import box

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.regions import Region
from rules.llm_input import build_items

ITEMS = ("S2", "E2", "E3", "E5")
POLICE = [
    place(201, "의성경찰서", "경찰서", 5000, sigungu="의성군"),
    place(202, "안동경찰서", "경찰서", 9000, sigungu="안동시"),
    place(203, "강릉경찰서", "경찰서", 9000, sigungu="강릉시"),
]
FIRE = [
    place(401, "의성소방서", "소방서", 3000, sigungu="의성군"),
    place(402, "안동소방서", "소방서", 9000, sigungu="안동시"),
    place(403, "강릉소방서", "소방서", 9000, sigungu="강릉시"),
]
UISEONG = {
    "sigungu": "의성군",
    "fire": "의성소방서",
    "police": "의성경찰서",
    "health": "의성군보건소",
}


def _items(**kw):
    kw.setdefault("police", POLICE)
    kw.setdefault("fire_stations", FIRE)
    return {i["id"]: i for i in build_items(build_bundles(build_context(full_inputs(**kw))))}


def _moved(sigungu):
    """예시 마을을 모두 다른 시군구로 옮긴다(리 자료가 없는 시군구는 엔진이 받지 않음)."""
    return [dataclasses.replace(v, sigungu=sigungu) for v in full_inputs().villages]


def _split(name_a, name_b, x):
    """x에서 동서로 나뉜 두 지역(서쪽 = name_a)."""
    return [Region(name_a, box(-1e5, -1e5, x, 1e5)), Region(name_b, box(x, -1e5, 1e5, 1e5))]


def test_uiseong_names_reach_each_item():
    items = _items()
    for item_id in ITEMS:
        assert items[item_id]["facts"]["jurisdiction"] == {
            "sido": "경상북도",
            "by_sigungu": [UISEONG],
        }, item_id
        assert not [w for w in items[item_id]["withheld"] if "관할" in w["field"]], item_id


def test_names_follow_fire_location():
    # 같은 엔진 · 같은 관서 자료, 산불만 강원 강릉시로 → 이름이 강릉 · 강원으로 바뀜(고정값이 아님)
    items = _items(
        sido=[Region("강원특별자치도", box(-1e5, -1e5, 1e5, 1e5))],
        sigungu=[Region("강릉시", box(-1e5, -1e5, 1e5, 1e5))],
        villages=_moved("강릉시"),
    )
    assert items["S2"]["facts"]["jurisdiction"] == {
        "sido": "강원특별자치도",
        "by_sigungu": [
            {
                "sigungu": "강릉시",
                "fire": "강릉소방서",
                "police": "강릉경찰서",
                "health": "강릉시보건소",
            }
        ],
    }


def test_spread_into_neighbor_lists_both():
    # P8이 x = 900 m 동쪽 안동시까지 걸침 → 시군구마다 한 줄
    villages = [
        *full_inputs().villages,
        village(98, "먼동리", "일직면", 5000, 5100, sigungu="안동시"),
    ]
    items = _items(sigungu=_split("의성군", "안동시", 900), villages=villages)
    assert items["E2"]["facts"]["jurisdiction"]["by_sigungu"] == [
        UISEONG,
        {
            "sigungu": "안동시",
            "fire": "안동소방서",
            "police": "안동경찰서",
            "health": "안동시보건소",
        },
    ]


def test_missing_station_is_null_and_withheld():
    # 청송군에 서 단위 관서 자료가 없음 → 이름을 지어내지 않고 null + 모르는 칸 · 언급 금지
    items = _items(sigungu=[Region("청송군", box(-1e5, -1e5, 1e5, 1e5))], villages=_moved("청송군"))
    for item_id in ITEMS:
        (row,) = items[item_id]["facts"]["jurisdiction"]["by_sigungu"]
        assert (row["fire"], row["police"], row["health"]) == (None, None, "청송군보건소")
        fields = [w["field"] for w in items[item_id]["withheld"]]
        assert {"청송군 관할 소방서", "청송군 관할 경찰서"} <= set(fields), item_id
        assert "청송군 관할 소방서" in items[item_id]["do_not_state"], item_id


def test_e3_gets_hospitals_for_transfer():
    e3 = _items()["E3"]
    assert e3["facts"]["hospitals"] == [
        {"name": "의료법인안동병원", "sigungu": "안동시", "km": 30.0}
    ]
    assert {"field": "응급의료기관 지정 등급", "reason": "자료 없음"} in e3["withheld"]
    (a4,) = e3["sections"][-1]["blocks"]
    assert (a4["topic"], a4["use"]) == ("거주자 이송", ["jurisdiction", "hospitals"])


def test_topics_point_at_jurisdiction():
    items = _items()
    use = {
        b["id"]: b["use"]
        for item_id in ITEMS
        for s in items[item_id]["sections"]
        for b in s["blocks"]
        if b["type"] == "action"
    }
    assert "jurisdiction" in use["S2-a2"]  # 주택 보호 배치 → 관할 소방서
    assert "jurisdiction" in use["E2-a1"]  # 위험구역 마을 대피 → 관할 경찰서
    assert "jurisdiction" in use["E5-a1"]  # 재난문자 · 자막방송 → 관할 시 · 도


def test_e6_health_row_still_from_home_sigungu():
    # 보건소 이름을 묶음 한 곳(agencies)에서 만들게 바꿨어도 E6 표 이름은 그대로
    e6 = _items()["E6"]
    table = next(b for s in e6["sections"] for b in s["blocks"] if b["type"] == "table")
    assert "의성군보건소장" in [r["agency"] for r in table["rows"]]
