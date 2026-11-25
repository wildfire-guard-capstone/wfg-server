"""마을(법정리): 범위에 걸친 리를 찾아 처음 닿는 P를 붙인다(R-02 · R-04 · R-05).

11/20 사용자 결정(D1 다시 정함): 「행정리에 불이 닿는 경우에 해당 행정리 전원 대피
대상임. 경계 걸침의 경우에는 대피 대상으로 간주.」
- 리 경계가 P8과 겹치면 대피 대상이다(「경계만 걸침」도 대상). 판정 P = **리 경계가 처음 닿는 P**.
  리 안 주택이 처음 닿는 P(`houses_by_p`)는 참고로만 남긴다
- 행정리(이장 단위)는 법정리 아래에 붙인다. 마을 구역이 있으면 **구역에 불이 처음 닿는 P**를 센다 —
  그 행정리 주민 전원이 대피 대상. 구역은 회관을 다 찾은 법정리에만 있다
  (11/25에 아래로 좁힘)
- `EdgeOnly`(11/19 밤판: 경계만 걸치고 8시간 안 주택이 없는 리를 대상에서 뺌)는 이제 만들지 않는다
  (빈 목록 — 묶음 · 항목의 「경계만 걸침」 칸은 비어 있게 된다)

11/25 사용자 결정: 불이 멀리 있는데 리가 같다고 리 전체를 대피시키지 않는다.
- 대상 리는 리 경계로 뽑고(11/20), 그 안에서 **불에 가까운 행정리**(`near_p`)만 남긴다.
  남는 행정리가 없으면 그 리는 대상이 아니다. 구역을 모르는 행정리가 있는 리는 리 전체를 그대로 둔다
- 판정 P = 남은 행정리 가운데 가장 이른 P. 불이 닿지 않으면(`touched` 거짓) 「○○까지 접근」이다
- 인원 = 리 인구 × 남은 행정리의 집 수 비율(추정, 행정리 인구 자료 없음)
- 구간(이미 닿음 · 위험구역 · 잠재 위험구역)은 변환 층의 `zone_of`가 P 번호로 정한다(J1, p.75)
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from rules.core.facility import Points
from rules.core.reach import MAX_P, first_p

NEAR_M = 1000.0  # 8시간 뒤 불(P8)에서 이 거리 안 = 잠재 위험구역(11/25 사용자 결정, 근거 등급 D)
CLOSE_M = 500.0  # 5시간 뒤 불(P5)에서 이 거리 안 = 위험구역(11/25, 근거 등급 D)
P5 = 5


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


def near_p(target: BaseGeometry, ranges: Sequence[BaseGeometry]) -> tuple[int | None, bool]:
    """불에 가까운 정도를 P로(행정리 구역 · 취약시설 자리)와 실제로 닿는지.

    1. 5시간 안에 닿으면 처음 닿는 P
    2. P5에서 `CLOSE_M` 안이면 P5(위험구역), 닿지 않음 — 5 ~ 8시간에 닿는 곳도 같은 자로 잰다
    3. 5 ~ 8시간에 닿으면 처음 닿는 P, P8에서 `NEAR_M` 안이면 P8(잠재 위험구역), 닿지 않음
    4. 더 멀면 None(대상 아님)
    """
    p = first_p(target, ranges)
    if p is not None and p <= P5:  # 1
        return p, True
    if target.distance(ranges[P5]) <= CLOSE_M:  # 2
        return P5, False
    if p is not None:  # 3
        return p, True
    if target.distance(ranges[MAX_P]) <= NEAR_M:
        return MAX_P, False
    return None, False  # 4


@dataclass(frozen=True)
class AdminReached:
    """행정리 하나: 불에 가까운 정도(`near_p`, 11/25). 구역이 없으면 p = None · houses = 0."""

    admin: AdminVillage
    p: int | None  # 불에서 멀거나(1 km 밖) 구역이 없으면 None
    houses: int  # 구역 안 주택 수
    touched: bool = True  # 구역에 실제로 불이 닿나(거짓 = 가까워지기만)


@dataclass(frozen=True)
class Reached:
    village: Village
    p: int  # 판정에 쓰는 P: 불에 가까운 행정리의 P(11/25). 행정리 구역을 모르면 리 경계 P
    boundary_p: int  # 리 경계가 처음 닿는 P
    houses: int  # 리 안 주택 수(0이면 `p` = 경계 P)
    houses_by_p: tuple[int, ...] = ()  # i번째 = Pi 안에 든 리 주택 수(누적)
    admin: tuple[AdminReached, ...] = ()  # 이 리의 행정리(별표 순서). 없으면 빈 묶음
    touched: bool = True  # `p`에 실제로 닿나(거짓 = 가까워지기만)

    @property
    def by_houses(self) -> bool:
        """`p`를 주택으로 정했나(False = 리 안 주택 자료 없음 → 경계 P)."""
        return self.houses > 0

    @property
    def by_admin(self) -> bool:
        """행정리 구역으로 판정했나(거짓 = 행정리 · 구역을 몰라 리 경계로 판정)."""
        return bool(self.admin) and all(a.admin.geom is not None for a in self.admin)

    @property
    def zoned(self) -> bool:
        """행정리가 둘 이상이고 모두 구역이 있다 → 행정리 단위로 나눠 볼 수 있다."""
        return len(self.admin) >= 2 and self.by_admin

    @property
    def reached_admin(self) -> list[AdminReached]:
        """대피 대상 행정리(별표 순). 나눠 볼 수 없으면 빈 목록."""
        return [a for a in self.admin if a.p is not None] if self.zoned else []

    @property
    def share(self) -> float:
        """대피 대상 행정리의 집 수 비율(리 인구를 나누는 데 씀). 나눠 볼 수 없으면 1."""
        total = sum(a.houses for a in self.admin)
        if not self.zoned or not total:
            return 1.0
        return sum(a.houses for a in self.reached_admin) / total

    @property
    def population(self) -> int:
        return round(self.village.population * self.share)

    @property
    def pop65_est(self) -> int | None:
        n = self.village.pop65_est
        return None if n is None else round(n * self.share)

    def unit_p(self, point: BaseGeometry) -> tuple[int | None, AdminReached | None]:
        """점(대피소)이 든 대피 단위의 P. None = 대피 대상 마을이 아님.

        1. 행정리로 나눠 볼 수 있으면 가장 가까운 행정리(구역 안이면 그 구역, 11/25)의 P
        2. 아니면 리의 P
        """
        if self.zoned:  # 1
            near = min(self.admin, key=lambda a: a.admin.geom.distance(point))
            return near.p, near
        return self.p, None  # 2

    def admin_at(self, point: BaseGeometry) -> AdminReached | None:
        """점이 구역 안에 든 행정리. 구역 밖이면 None(가까운 곳으로 짐작하지 않음)."""
        zones = (a for a in self.admin if a.admin.geom is not None)
        return next((a for a in zones if a.admin.geom.intersects(point)), None)


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
    """P8 안 마을(리 경계 기준, 불에 가까운 행정리가 있는 곳)과 경계만 걸친 마을(늘 빈 목록).

    1. 리 경계가 처음 닿는 P를 찾는다(R-04). P8과도 안 겹치면 뺀다
    2. 리 안 주택이 Pi마다 몇 동 드는지 센다(참고 — 시설피해 · 보고용)
    3. 행정리를 붙인다: 구역이 있으면 `near_p` · 구역 주택 수
    4. 행정리 구역을 모두 알면 가장 이른 행정리의 P로 판정하고, 가까운 행정리가 없으면 뺀다(11/25).
       구역을 모르면 리 경계 P로 판정한다
    5. 닿는 순(같은 P면 실제로 닿는 곳 먼저), 같으면 이름 순으로 놓는다
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
        units = []
        for a in by_village.get(v.id, []):  # 3
            if a.geom is None:
                units.append(AdminReached(a, None, 0))
                continue
            zin = _inside(a.geom, hx, hy)
            p, touched = near_p(a.geom, ranges)
            units.append(AdminReached(a, p, int(zin.sum()), touched))
        hit = Reached(v, bp, bp, n, counts, tuple(units))
        if hit.by_admin:  # 4
            near = [u for u in units if u.p is not None]
            if not near:
                continue
            first = min(near, key=lambda u: (u.p, not u.touched))
            hit = replace(hit, p=first.p, touched=first.touched)
        hits.append(hit)
    hits.sort(key=lambda r: (r.p, not r.touched, r.village.name))  # 5
    edges.sort(key=lambda e: (e.boundary_p, e.village.name))
    return hits, edges
