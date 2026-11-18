# ruff: noqa: E501
"""원자료 표(C1 · C2 입력, 11/19 재설계 6장 표 9): 넣는 것 · 주지 않는 것 · 좌표 꼴 · 반경(D6) · 중심 좌표(D5)."""

from datetime import datetime
from types import SimpleNamespace as NS

from shapely.geometry import LineString, Point, box

from demo.uiseong import raw_table
from demo.uiseong.data import to5179
from rules.core.places import Deployed

IG = to5179(Point(128.6, 36.37))  # 발화점(EPSG:5179)


def _place(name, kind, x, y, **kw):
    return NS(name=name, kind=kind, geom=Point(IG.x + x, IG.y + y), **kw)


def _village(i, name, dx, pop):
    return NS(id=i, name=name, emd="안평면", sigungu="의성군", population=pop,
              geom=box(IG.x + dx, IG.y, IG.x + dx + 500, IG.y + 500))  # fmt: skip


class MD:
    ignition = IG
    perimeters = {"ignition": {"at": "11:24"}}
    neighbors = {"의성군": ["안동시"]}
    township_ids = {99}
    villages = [
        _village(1, "가까운리", 0, 100),
        _village(2, "먼리", 20_000, 50),
        _village(99, "일직면 전체", 1000, 9),
    ]
    vulnerable = [_place("요양원", "노인요양원", 100, 0)]
    shelters = [_place("학교", "학교", 200, 0, capacity=300)]
    heritage = [_place("석탑", "국보", 300, 0)]
    substations = [_place("변전소", "변전소", 400, 0)]
    power_lines = [NS(voltage_kv=154, geom=LineString([(IG.x, IG.y), (IG.x + 5000, IG.y + 10)]))]
    roads = [
        NS(name="가까운로", grade="primary", geom=LineString([(IG.x, IG.y), (IG.x + 3000, IG.y)])),
        NS(
            name="먼로",
            grade="primary",
            geom=LineString([(IG.x + 30_000, IG.y), (IG.x + 31_000, IG.y)]),
        ),
    ]
    fire_stations = [_place("의성소방서", "소방서", 500, 0, sigungu="의성군")]
    police = [_place("의성경찰서", "경찰서", 600, 0, sigungu="의성군")]
    hospitals = [_place("병원", "병원", 700, 0, sigungu="의성군")]
    bases = [NS(kind="산림청 헬기", sigungu="안동시", geom=Point(IG.x + 9000, IG.y))]
    LAYERS = {
        "03": [{"code": "1", "houses": 40}, {"code": "2", "houses": 5}],
        "07": [{"name": "요양원", "group": "노인요양원", "cap": 29, "now": 24}],
        "12": [{"name": "병원", "kind": "병원", "dist_km": 7.5, "in_uiseong": True}],
        "10": [{"name": "안동산림항공관리소", "n": 5, "models": "KA-32T", "km": 22.3}],
    }

    def _layer(self, prefix):
        return [(p, None) for p in self.LAYERS[prefix]]


RD = NS(base_at=datetime(2025, 3, 22, 12, 50), report="1보", at="12:50", reader="의성군수")
SPREAD = NS(ranges=[box(IG.x, IG.y, IG.x + 300 * (h + 1), IG.y + 300) for h in range(9)])
INPUTS = {
    "wind_ms": {"0": 3.4, "1": 6.1},
    "wind_deg": {"0": 270, "1": 292},
    "temp_c": 22.9,
    "rh_pct": 22,
    "field": {
        "가까운리": {
            "alert_at": "12:40",
            "evac_order": False,
            "evacuated": False,
            "reported_at": "12:50",
        }
    },
    "deployed": {
        "helicopter": {"산림청": 5},
        "crew": {"진화대": 81},
        "vehicle": {"소방차": 15},
        "reported_at": "12:50",
    },
}


def test_raw_table_has_table9_items_and_no_precomputed_values():
    text = raw_table.build(MD(), RD, SPREAD, INPUTS, None, ())
    for head in ("## 확산 예측 P0 ~ P8", "## 기상", "## 현장 입력", "## 투입 자원", "## 직전 제안서", "## 마을 2곳",
                 "## 취약시설", "## 대피소 후보", "## 국가유산", "## 송전선", "## 큰 도로", "## 소방", "## 경찰",
                 "## 응급 의료기관", "## 진화헬기 기지", "일출 · 일몰"):  # fmt: skip
        assert head in text, head
    assert "P8(~20:50)" in text and "13:50 | 6.1 | 292°(서북서)" in text
    assert "요양원 | 노인요양원 | 29 | 24 |" in text  # 정원 · 현원
    assert "안동산림항공관리소 | 산림청 헬기 | 안동시 | 5 | KA-32T |" in text  # 대수 · 기종
    # 주지 않는 것: 미리 계산된 거리 · 안팎 칸, 의성 밖 읍면 대용 마을, 10 km 밖 도로
    assert "22.3" not in text and "7.5" not in text and "True" not in text
    assert "일직면 전체" not in text and "먼로" not in text and "가까운로" in text
    assert "대피 완료 보고 없음" in text and "마지막 대피 문자 12:40" in text
    assert "없음(이번이 첫 제안서)" in text


def test_village_boundary_only_within_10km_and_centers_only_option():
    rows = {
        ln.split(" | ")[0]: ln
        for ln in raw_table.build(MD(), RD, SPREAD, INPUTS).splitlines()
        if " | 의성군 | " in ln
    }
    assert rows["가까운리"].count(";") >= 4 and rows["먼리"].count(";") == 0  # 10 km 안만 경계(D6)
    assert "| 40 |" in rows["가까운리"]  # 마을별 주택 수
    centers = raw_table.build(MD(), RD, SPREAD, INPUTS, centers_only=True)
    near = next(ln for ln in centers.splitlines() if ln.startswith("가까운리 | "))
    assert near.count(";") == 0 and "경계(" not in centers  # D5: 중심 좌표만


def test_coordinates_are_wgs84_five_decimals_and_prev_round():
    text = raw_table.build(MD(), RD, SPREAD, INPUTS, 1, (("의성군", "안평면", "go"),))
    assert "발화점 128.60000 36.37000" in text
    assert "직전 단계: 1단계" in text and "의성군 안평면(Go)" in text
    assert (
        raw_table.compass(270) == "서"
        and raw_table.compass(0) == "북"
        and raw_table.compass(359) == "북"
    )


def test_deployed_type_matches_rules():
    Deployed(helicopter={"산림청": 5})  # 투입 자원 칸 이름은 규칙 엔진과 같은 라벨을 쓴다
