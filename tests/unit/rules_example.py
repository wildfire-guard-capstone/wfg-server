"""규칙 테스트용 12:50 예시 입력(합성 도형). 범위는 원점을 중심으로 한 정사각형이다."""

from datetime import datetime
from math import sqrt

from shapely.geometry import Point, box

from rules.context import Inputs
from rules.core.places import Place
from rules.core.regions import Region
from rules.core.villages import Village

BASE = datetime(2025, 3, 22, 12, 50)
# 12:50 예시 면적(ha): P0(현재 화선) · P1 ~ P8
AREAS = [48.5, 89, 205, 262, 316, 363, 410, 455, 499]
WORLD = box(-1e5, -1e5, 1e5, 1e5)
SITE = (36.36, 128.70)  # 의성군 안평면 발화점 근처(위도 · 경도) → 3/22 일출 06:28 · 일몰 18:38


def half(ha: float) -> float:
    """면적이 ha인 정사각형의 반 변(m, EPSG:5179)."""
    return sqrt(ha * 10_000) / 2


def square(ha: float):
    h = half(ha)
    return box(-h, -h, h, h)


def strip(x0: float, x1: float):
    """x 방향 띠 모양 마을(y는 -50 ~ 50 m). 범위 반 변과 비교해 처음 닿는 P를 정한다."""
    return box(x0, -50, x1, 50)


def village(id_, name, emd, x0, x1, population=100, sigungu="의성군"):
    return Village(id_, name, emd, sigungu, strip(x0, x1), population, None)


# 범위 반 변(m): P0 348 · P1 472 · P2 716 · P5 953 · P8 1117
VILLAGES = [
    village(1, "괴산리", "안평면", 300, 400, 147),  # P0에 걸침 → 이미 닿음
    village(2, "신월리", "안평면", -400, -300, 128),  # P0
    village(3, "신안리", "안평면", 400, 460, 181),  # P1
    village(4, "철파리", "의성읍", 700, 800, 341),  # P2
    village(5, "정자리", "옥산면", 1100, 1200, 114),  # P8 → 잠재 위험구역
    village(6, "먼리", "옥산면", 1200, 1300),  # P8 밖 → 빠짐
]


def place(id_, name, kind, x, y=0.0, **kw) -> Place:
    return Place(id_, name, kind, Point(x, y), **kw)


# 취약시설: 철파리(P2) 2곳 · 정자리(P8) 1곳 · 먼리(P8 밖) 1곳
VULNERABLE = [
    place(55, "의성군공립요양병원", "요양병원", 750, village_id=4),
    place(56, "의성행복한노인복지센터", "주야간보호", 760, village_id=4, daytime_only=True),
    place(
        58,
        "의료법인 해안의료재단 해안1병원",
        "정신병원",
        770,
        village_id=4,
        short_name="해안1병원",
    ),
    place(61, "정자요양원", "노인요양원", 1150, village_id=5),
    place(62, "먼요양원", "노인요양원", 1250, village_id=6),
]


def inputs(**kw) -> Inputs:
    base = dict(
        base_at=BASE,
        generated_at=BASE,
        reader="의성군수",
        prev_stage=None,
        ranges=[square(ha) for ha in AREAS],
        ignition=Point(0, 0),
        wind_ms={0: 3.4, **{h: 2.0 for h in range(1, 9)}},
        wind_deg={h: 270.0 for h in range(9)},  # 서풍
        houses=([-350.0, -340.0], [0.0, 0.0]),  # 신월리 안 · P5 안 주택 2동
        sido=[Region("경상북도", WORLD)],
        sigungu=[Region("의성군", WORLD)],
        villages=[village(99, "먼리", "옥산면", 5000, 5100)],  # 의성군 리 자료(P8 밖)
        site=SITE,
        field={},
    )
    base.update(kw)
    return Inputs(**base)
