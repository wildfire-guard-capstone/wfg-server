"""마을(법정리): 범위에 걸친 리를 찾아 처음 닿는 P를 붙인다(R-02 · R-04 · R-05).

11/19 평가 R3: 리 경계가 범위와 조금만 겹쳐도 리 전체를 대상으로 보던 것(10/29)을 바꿔,
**리 안 주택이 처음 닿는 P**로 판정한다. 회차 4에서 리 경계로는 위험구역 9곳 4,217명이었는데
P5 안 주택은 13동이었다(산자락만 걸친 리가 리 인구 전체로 잡힘).

- 경계는 P8과 겹치는데 8시간 안에 주택이 하나도 안 닿는 리 = 「경계만 걸침」(`EdgeOnly`).
  대피 대상에서 빼고 따로 보여 준다
- 리 안에 주택 자료가 없으면 경계 P를 그대로 쓴다(사람이 어디 사는지 모르니 넓게)
- 행정리(이장 단위)는 법정리 아래에 붙인다. 마을 구역(주택 무리)이 있으면 구역마다 처음 닿는 P를
  센다. 구역은 회관을 다 찾은 법정리에만 있다(그 법정리의 행정리 모두에 구역이 있음)
- 구간(이미 닿음 · 위험구역 · 잠재 위험구역)은 변환 층의 `zone_of`가 P 번호로 정한다(J1, p.75)
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from rules.core.facility import Points
from rules.core.reach import MAX_P, first_p


@dataclass(frozen=True)
class Village:
    id: int
    name: str  # 리 이름
    emd: str  # 속한 읍면
    sigungu: str  # 속한 시군구
    geom: BaseGeometry  # 리 경계, EPSG:5179
    population: int  # 주민등록 인구
    pop65_est: int | None  # 65세 이상 추정(집계구 비율로 나눔). 없으면 None


@dataclass(frozen=True)
class AdminVillage:
    """행정리(「의성군 이장 정수 조례」 별표). 법정리 아래 이장 단위."""

    id: int
    village_id: int  # 속한 법정리
    name: str  # 신안1리
    seq: int  # 별표 순서
    hall_name: str | None = None  # 마을회관 · 경로당 이름
    hall: BaseGeometry | None = None  # 회관 점(EPSG:5179). 모르면 None
    geom: BaseGeometry | None = None  # 마을 구역(회관에서 시작한 K-평균 주택 무리). 없으면 None


@dataclass(frozen=True)
class VillageField:
    """현장 입력(상황 정정): 마을별 대피 상태. 입력이 없으면 기본값(거짓 · None)."""

    evac_order: bool = False  # 대피명령
    evacuated: bool = False  # 대피 완료
    pre_evac_done: bool = False  # 사전대피(고령자 등) 완료
    alert_at: str | None = None  # 마지막 대피 문자 발송 시각(「12:40」)
    reported_at: str | None = None  # 이 값들을 마지막으로 보고한 시각


@dataclass(frozen=True)
class AdminReached:
    """행정리 하나: 구역 안 주택이 처음 닿는 P. 구역이 없으면 p = None · houses = 0."""

    admin: AdminVillage
    p: int | None  # 구역 안 주택이 처음 닿는 P. 8시간 안에 안 닿거나 구역이 없으면 None
    houses: int  # 구역 안 주택 수


@dataclass(frozen=True)
class Reached:
    village: Village
    p: int  # 판정에 쓰는 P: 리 안 주택이 처음 닿는 P(주택 자료가 없으면 경계 P)
    boundary_p: int  # 리 경계가 처음 닿는 P(참고)
    houses: int  # 리 안 주택 수(0이면 `p` = 경계 P)
    houses_by_p: tuple[int, ...] = ()  # i번째 = Pi 안에 든 리 주택 수(누적)
    admin: tuple[AdminReached, ...] = ()  # 이 리의 행정리(별표 순서). 없으면 빈 묶음

    @property
    def by_houses(self) -> bool:
        """`p`를 주택으로 정했나(False = 리 안 주택 자료 없음 → 경계 P)."""
        return self.houses > 0

    @property
    def zoned(self) -> bool:
        """행정리가 둘 이상이고 모두 구역이 있다 → 행정리 단위로 나눠 볼 수 있다."""
        return len(self.admin) >= 2 and all(a.admin.geom is not None for a in self.admin)

    @property
    def reached_admin(self) -> list[AdminReached]:
        """8시간 안에 주택이 닿는 행정리(닿는 순, 같으면 별표 순). 나눠 볼 수 없으면 빈 목록."""
        if not self.zoned:
            return []
        hit = [a for a in self.admin if a.p is not None]
        return sorted(hit, key=lambda a: (a.p, a.admin.seq))

    def unit_p(self, point: BaseGeometry) -> tuple[int | None, AdminReached | None]:
        """점(시설 · 대피소)이 든 대피 단위의 P.

        1. 행정리 구역으로 나눠 볼 수 있으면 점이 든 구역의 P(구역 밖이면 None — 주택 무리 밖)
        2. 아니면 리의 P
        """
        if self.zoned:  # 1
            for a in self.admin:
                if a.admin.geom is not None and a.admin.geom.intersects(point):
                    return a.p, a
            return None, None
        return self.p, None  # 2


@dataclass(frozen=True)
class EdgeOnly:
    """경계만 걸친 리: 리 경계는 P8과 겹치지만 8시간 안에 닿는 주택이 없다."""

    village: Village
    boundary_p: int
    houses: int


def _xy(houses: Points) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = houses
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


def _inside(area: BaseGeometry, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    if len(xs) == 0:
        return np.zeros(0, dtype=bool)
    return shapely.intersects_xy(area, xs, ys)


def houses_by_p(xs: np.ndarray, ys: np.ndarray, ranges: Sequence[BaseGeometry]) -> tuple[int, ...]:
    """점들이 Pi 안에 몇 개 드는지(i = 0 ~ 8, 경계 위 점도 셈). 범위가 누적이라 값도 누적."""
    return tuple(int(_inside(r, xs, ys).sum()) for r in ranges[: MAX_P + 1])


def first_hit(counts: Sequence[int]) -> int | None:
    """누적 개수에서 처음 1 이상이 되는 P. 하나도 없으면 None."""
    return next((p for p, n in enumerate(counts) if n), None)


def reached_villages(
    villages: Sequence[Village],
    ranges: Sequence[BaseGeometry],
    houses: Points = ((), ()),
    admin: Sequence[AdminVillage] = (),
) -> tuple[list[Reached], list[EdgeOnly]]:
    """P8 안 마을(주택 기준)과 경계만 걸친 마을.

    1. 리 경계가 처음 닿는 P를 찾는다(R-04). P8과도 안 겹치면 뺀다
    2. 리 안 주택이 Pi마다 몇 동 드는지 센다
       - 리 안 주택이 없으면 경계 P를 쓴다
       - 주택은 있는데 8시간 안에 하나도 안 닿으면 「경계만 걸침」
       - 아니면 주택이 처음 닿는 P
    3. 행정리를 붙인다: 구역이 있으면 구역 안 주택이 처음 닿는 P · 구역 주택 수
    4. 닿는 순, 같으면 이름 순으로 놓는다(경계만 걸침은 경계 P 순)
    """
    xs, ys = _xy(houses)
    by_village: dict[int, list[AdminVillage]] = {}
    for a in sorted(admin, key=lambda a: a.seq):
        by_village.setdefault(a.village_id, []).append(a)
    hits: list[Reached] = []
    edges: list[EdgeOnly] = []
    for v in villages:
        bp = first_p(v.geom, ranges)  # 1
        if bp is None:
            continue
        inside = _inside(v.geom, xs, ys)  # 2
        hx, hy = xs[inside], ys[inside]
        n = len(hx)
        counts = houses_by_p(hx, hy, ranges) if n else ()
        hp = first_hit(counts) if n else bp
        if hp is None:
            edges.append(EdgeOnly(v, bp, n))
            continue
        units = []
        for a in by_village.get(v.id, []):  # 3
            if a.geom is None:
                units.append(AdminReached(a, None, 0))
                continue
            zin = _inside(a.geom, hx, hy)
            units.append(
                AdminReached(a, first_hit(houses_by_p(hx[zin], hy[zin], ranges)), int(zin.sum()))
            )
        hits.append(Reached(v, hp, bp, n, counts, tuple(units)))
    hits.sort(key=lambda r: (r.p, r.village.name))  # 4
    edges.sort(key=lambda e: (e.boundary_p, e.village.name))
    return hits, edges
