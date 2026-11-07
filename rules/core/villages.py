"""마을(법정리): 범위에 걸친 리를 찾아 처음 닿는 P를 붙인다(R-02 · R-04 · R-05).

리 영역이 겹치면 리 전체가 대상이다(10/29). 구간(이미 닿음 · 위험구역 · 잠재 위험구역)은
변환 층의 `zone_of`가 P 번호로 정한다(J1, 매뉴얼 p.75).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from rules.core.reach import first_p


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
class VillageField:
    """현장 입력(상황 정정): 마을별 대피 상태. 입력이 없으면 기본값(거짓 · None)."""

    evac_order: bool = False  # 대피명령
    evacuated: bool = False  # 대피 완료
    pre_evac_done: bool = False  # 사전대피(고령자 등) 완료
    alert_at: str | None = None  # 마지막 대피 문자 발송 시각(「12:40」)
    reported_at: str | None = None  # 이 값들을 마지막으로 보고한 시각


@dataclass(frozen=True)
class Reached:
    village: Village
    p: int  # 처음 닿는 P(0 ~ 8)


def reached_villages(villages: Sequence[Village], ranges: Sequence[BaseGeometry]) -> list[Reached]:
    """P8 안에 걸친 마을과 처음 닿는 P.

    1. 마을마다 처음 닿는 P를 찾는다(R-04)
    2. P8과도 안 겹치는 마을은 뺀다
    3. 닿는 순, 같으면 이름 순으로 놓는다(「65세 이상 많은 순」은 11/7에 지움)
    """
    hits = [Reached(v, p) for v in villages if (p := first_p(v.geom, ranges)) is not None]  # 1 · 2
    return sorted(hits, key=lambda r: (r.p, r.village.name))  # 3
