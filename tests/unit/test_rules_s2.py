"""S2: 송전선 규칙 · 점 시설 규칙 · 리별 주택, S2는 고르기만 하는지(핵심 경우만)."""

from rules_example import VILLAGES, VULNERABLE, inputs, place
from shapely.geometry import LineString, box

from rules.bundles import build_bundles
from rules.context import build_context
from rules.core.places import PowerLine
from rules.core.villages import Village
from rules.items.s2 import s2_input
from rules.sections.s2 import s2_sections

# 송전선이 지나는 띠 마을(y 600 ~ 800). 범위 반 변: P1 472 · P2 716 · P5 953 · P8 1117
LINE_VILLAGES = [
    Village(21, "학미리", "점곡면", "의성군", box(-2000, 600, -1000, 800), 50, None),
    Village(22, "도옥리", "안평면", "의성군", box(-1000, 600, 0, 800), 50, None),
    Village(23, "용연리", "안평면", "의성군", box(0, 600, 2000, 800), 50, None),
]
# y = 700 가로선: P2(716)에 처음 닿음, 5시간 안 -953 ~ 953 · 8시간 안 -1117 ~ 1117
LINE = PowerLine(3, 154, LineString([(-1500, 700), (1500, 700)]))
POLICE = [place(201, "의성경찰서", "경찰서", 5000), place(202, "안평파출소", "파출소", 430)]


def _run(**kw):
    base = dict(villages=VILLAGES + LINE_VILLAGES, vulnerable=VULNERABLE, police=POLICE)
    bundles = build_bundles(build_context(inputs(**{**base, **kw})))
    s2 = s2_input(bundles)
    return bundles, s2, s2_sections(s2)


def test_power_line_rule():
    bundles, s2, _ = _run(power_lines=[LINE])
    (line,) = bundles["protected"]["power_lines"]
    # 이름 = 5시간 부분의 바깥 두 끝점이 든 리 · 8시간에서 새로 걸리는 리 = 학미리
    assert line["name"] == "154 kV 송전선 도옥리~용연리 구간"
    assert (line["first_p"], line["from"], line["until"]) == (2, "13:50", "14:50")
    assert (line["km_p5"], line["km_p8"], line["p8_adds"]) == (1.9, 2.2, ["학미리"])
    assert s2["facts"]["power"][0]["p8_until"] == "20:50"


def test_points_houses_and_exclusions():
    temple = place(901, "운람사", "전통사찰", 420, village_id=3)  # 신안리 · P1
    far_heritage = place(301, "의성석탑리방단형적석탑", "보물", 3000)  # P8 밖
    solar = place(402, "팔성리 태양광", "태양광", 100)  # P0 안이지만 뺌
    bundles, s2, sec = _run(temples=[temple], heritage=[far_heritage], substations=[solar])
    pr = bundles["protected"]
    (t,) = pr["temples"]
    assert (t["village"], t["zone"], t["until"]) == ("신안리", "p5", "13:50")
    assert t["police"]["name"] == "의성경찰서"  # 파출소는 관서가 아님
    assert pr["heritage"] == {"total": 1, "in_p8": 0, "items": []}
    assert pr["substations"] == []
    assert {"field": "사찰 소장 문화유산의 지정 여부", "reason": "정보 없음"} in pr["unknown"]
    # 리별 주택: 신월리 2동(합계와 같음)
    assert s2["facts"]["houses"]["in_p5"] == [{"village": "신월리", "houses": 2}]
    assert s2["status"] == ["life", "infrastructure", "property"]
    assert s2["finding"].endswith("② 운람사(전통사찰) ③ 주택 신월리 최소 2동")
    rows = sec["sections"][0]["blocks"][1]["rows"]
    assert [r["rank"] for r in rows] == [1, 2, 3]
    life = rows[0]["text"]
    assert life.startswith("① **인명**: 지금(이미 닿음) 안평면 괴산리·신월리, ")
    assert "의성읍 철파리(의성군공립요양병원·의성행복한노인복지센터·해안1병원 주변)" in life
    labels = [b["label"] for b in sec["sections"][1]["blocks"]]
    assert labels == ["주택", "문화유산"]  # 송전선 없음 → 송전시설 블록 없음
    assert sec["sections"][0]["blocks"][2]["text"] == (
        "※ 군사시설과 중요 산림자원은 위치 정보가 없어 반영하지 않았습니다."
    )
