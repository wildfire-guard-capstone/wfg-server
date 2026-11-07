"""데모 회차(2025-03-22 의성, 상황도 1 ~ 4보)와 회차별 현장 입력.

값은 「2025년 의성산불백서」 부록 3 산불 상황 보고(인쇄 p.374)와 시나리오 v1(11/6)의 기록이다.
- 투입 수 · 기상(풍향 · 평균풍속 · 기온 · 습도)은 상황 보고 1 ~ 4보 그대로
- 대피 문자 · 대피명령은 시나리오 v1 기록: 신월리 12:40 · 12:51 문자(백서 p.350),
  13:45 회차부터 대피명령으로 입력
- 읽는 사람: 12:50 ~ 13:45 의성군수, 14:20 경상북도지사(시나리오 v1 회차와 같음, 11/7 사용자 결정)
"""

from dataclasses import dataclass, field
from datetime import datetime

from rules.core.places import Deployed
from rules.core.villages import VillageField

DAY = datetime(2025, 3, 22)
BAND1 = datetime(2025, 3, 22, 11, 0)  # ELMFIRE 입력의 기상 밴드 1
SINWOL = "신월리"


@dataclass(frozen=True)
class Round:
    no: int
    version: str
    at: str  # 기준 시각(화선 보고)
    report: str  # 상황도 몇 보
    reader: str
    deployed: Deployed
    wind_dir_deg: float  # 상황 보고 풍향(불어오는 쪽)
    wind_ms: float  # 상황 보고 평균풍속
    temp_c: float
    rh_pct: float
    field_by_name: dict[str, VillageField] = field(default_factory=dict)
    note: str = ""

    @property
    def base_at(self) -> datetime:
        h, m = map(int, self.at.split(":"))
        return DAY.replace(hour=h, minute=m)

    @property
    def t_s(self) -> float:
        """ELMFIRE 시각(기상 밴드 1 뒤 초)."""
        return (self.base_at - BAND1).total_seconds()


def _dep(heli, crew, veh, at):
    return Deployed(
        helicopter=heli,
        crew={"진화대": crew[0], "공무원": crew[1], "소방": crew[2]},
        vehicle={"지휘·진화차": veh[0], "소방차": veh[1], "기타": veh[2]},
        reported_at=at,
    )


ROUNDS = [
    Round(
        1,
        "v1.1",
        "12:50",
        "1보",
        "의성군수",
        _dep({"산림청": 5, "지자체": 16, "소방": 2}, (81, 13, 57), (10, 15, 1), "12:50"),
        270.0,
        3.4,
        22.9,
        22,
        {SINWOL: VillageField(alert_at="12:40", reported_at="12:50")},
        "첫 화선 보고(산불영향구역 조사 중)",
    ),
    Round(
        2,
        "v2.1",
        "13:10",
        "2보",
        "의성군수",
        _dep({"산림청": 5, "지자체": 16, "소방": 2}, (149, 13, 57), (10, 15, 1), "13:10"),
        270.0,
        3.4,
        22.9,
        22,
        {SINWOL: VillageField(alert_at="12:51", reported_at="13:10")},
        "화선 51.0 ha",
    ),
    Round(
        3,
        "v3.1",
        "13:45",
        "3보",
        "의성군수",
        _dep({"산림청": 8, "지자체": 17, "소방": 2, "군": 1}, (187, 113, 57), (13, 21, 2), "13:45"),
        270.0,
        4.9,
        24.7,
        19,
        {SINWOL: VillageField(evac_order=True, alert_at="12:51", reported_at="13:45")},
        "화선 73.8 ha, 신월리 대피 문자 2회 → 대피명령으로 입력",
    ),
    Round(
        4,
        "v4.1",
        "14:20",
        "4보",
        "경상북도지사",
        _dep({"산림청": 9, "지자체": 17, "소방": 2}, (205, 113, 57), (13, 21, 2), "14:20"),
        270.0,
        4.9,
        24.7,
        19,
        {SINWOL: VillageField(evac_order=True, alert_at="12:51", reported_at="14:20")},
        "화선 177 ha, 지휘권 전환 뒤 첫 보고",
    ),
]

INCIDENT = {
    "name": "의성군 안평면 산불 (2025-03-22)",
    "sigungu": "의성군",
    "emd": "안평면",
    "address": "경북 의성군 안평면 괴산리 산61 일원",
    "started_at": "2025-03-22 11:24",
    "reported_at": "2025-03-22 11:25",
    "status": "진행 중",
}
