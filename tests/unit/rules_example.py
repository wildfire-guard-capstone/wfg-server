"""규칙 테스트용 12:50 예시 입력(합성 도형). 범위는 원점을 중심으로 한 정사각형이다."""

from datetime import datetime
from math import sqrt

from shapely.geometry import box

from rules.context import Inputs
from rules.core.regions import Region
from rules.core.villages import Village

BASE = datetime(2025, 3, 22, 12, 50)
# 12:50 예시 면적(ha): P0(현재 화선) · P1 ~ P8
AREAS = [48.5, 89, 205, 262, 316, 363, 410, 455, 499]
WORLD = box(-1e5, -1e5, 1e5, 1e5)


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


def inputs(**kw) -> Inputs:
    base = dict(
        base_at=BASE,
        generated_at=BASE,
        reader="의성군수",
        prev_stage=None,
        ranges=[square(ha) for ha in AREAS],
        wind_ms={0: 3.4, **{h: 2.0 for h in range(1, 9)}},
        houses=([0.0, 10.0], [0.0, 10.0]),  # P5 안 주택 2동
        heritage=([], []),
        sido=[Region("경상북도", WORLD)],
        sigungu=[Region("의성군", WORLD)],
        villages=[village(99, "먼리", "옥산면", 5000, 5100)],  # 의성군 리 자료(P8 밖)
    )
    base.update(kw)
    return Inputs(**base)
