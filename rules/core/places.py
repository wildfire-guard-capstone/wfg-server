"""엔진이 받는 지도 · 현장 자료형. 어댑터(app)가 DB에서 읽어 이 모양으로 넘긴다.

- `Place`: 점 시설 하나(`facilities` · `map_features` 점). 종류마다 쓰는 칸만 채운다
- `PowerLine`: 송전선 하나(선로 단위) · `RoadPiece`: 큰 도로 조각 하나(OSM)
- `Base`: 진화자원 기지 · `Deployed`: 상황 보고의 투입 수
좌표는 모두 EPSG:5179(m).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class Place:
    id: int
    name: str
    # 세부 종류: 노인요양원 · 경로당 · 학교 · 경찰서 · 소방서 · 보물 · 전통사찰 · 변전소 …
    kind: str | None
    geom: BaseGeometry  # 위치(점, 넓은 시설은 면도 됨)
    village_id: int | None = None  # 들어 있는 리(관할 밖 시설은 None)
    sigungu: str | None = None  # 들어 있는 시 · 군 · 구
    daytime_only: bool = False  # 취약시설: 낮에만 운영(주야간보호)
    emergency: bool = False  # 병원: 응급실 있음(응급 이송처)
    capacity: int | None = None  # 대피소: 수용 인원(없으면 모름)
    tier: str | None = None  # 대피소: village(마을 시설) · hub(거점 시설) · None(후보 아님, 모텔)


@dataclass(frozen=True)
class PowerLine:
    id: int
    voltage_kv: int  # 345 · 154
    geom: BaseGeometry  # 선


@dataclass(frozen=True)
class RoadPiece:
    id: int
    grade: str  # OSM highway: motorway · trunk · primary · secondary · tertiary (+ _link)
    name: str | None  # 도로명
    ref: str | None  # 노선 번호(숫자만, 「5;28」 = 두 노선이 겹친 구간)
    geom: BaseGeometry  # 선


@dataclass(frozen=True)
class Base:
    id: int
    name: str  # 안동산림항공관리소 · 의성소방서 …
    kind: str  # 산림청 헬기 · 지자체 임차헬기 · 소방 헬기 · 지상 소방 · 지상 진화대
    sigungu: str  # 기지가 있는 시 · 군 · 구
    sido: str  # 기지가 있는 시 · 도
    coverage: Sequence[str]  # 관할 시 · 군 · 구
    geom: BaseGeometry  # 점
    helicopters: int | None = None  # 부를 수 있는(대기) 헬기 수. 모르면 None
    vehicles: int | None = None  # 부를 수 있는 차량 수. 모르면 None
    deployed_helicopters: int | None = None  # 이 산불에 투입된(자원 연결) 헬기 수. 모르면 None
    deployed_vehicles: int | None = None  # 이 산불에 투입된(자원 연결) 차량 수. 모르면 None


@dataclass(frozen=True)
class Deployed:
    """상황 보고(산림청 양식)의 투입 수. 라벨은 묶음이 고정하고, 없는 라벨은 0."""

    # 헬기: 산림청 · 지자체 · 소방 · 군 · 경찰 / 인력: 진화대 · 공무원 · 소방 · 군 · 경찰 · 기타
    helicopter: Mapping[str, int] = field(default_factory=dict)
    crew: Mapping[str, int] = field(default_factory=dict)
    vehicle: Mapping[str, int] = field(default_factory=dict)  # 지휘·진화차 · 소방차 · 기타
    reported_at: str | None = None  # 보고 시각
