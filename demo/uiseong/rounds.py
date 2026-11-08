"""데모 회차와 회차별 현장 입력: 입력 폴더의 scenario.json을 읽는다.

    sc = load(Path("…/demo-uiseong/input"))
    sc.rounds[0].at, sc.rounds[0].t_s, sc.incident, sc.ignition_t_s

scenario.json의 값은 「2025년 의성산불백서」 부록 3 산불 상황 보고(인쇄 p.374)와 시나리오 v1(11/6)의
기록이다(출처는 파일 안 sources). 회차 · 읽는 사람은 11/7 사용자 결정.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rules.core.places import Deployed
from rules.core.villages import VillageField


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
    day: datetime = datetime(2025, 3, 22)
    band1: datetime = datetime(2025, 3, 22, 11, 0)  # ELMFIRE 입력의 기상 밴드 1

    @property
    def base_at(self) -> datetime:
        h, m = map(int, self.at.split(":"))
        return self.day.replace(hour=h, minute=m)

    @property
    def t_s(self) -> float:
        """ELMFIRE 시각(기상 밴드 1 뒤 초)."""
        return (self.base_at - self.band1).total_seconds()


@dataclass(frozen=True)
class Scenario:
    title: str
    incident: dict
    rounds: list[Round]
    ignition_t_s: float  # 발화 시각(ELMFIRE 시각)
    sources: dict = field(default_factory=dict)


def _hm(day: datetime, hm: str) -> datetime:
    h, m = map(int, hm.split(":"))
    return day.replace(hour=h, minute=m)


def parse(data: dict) -> Scenario:
    t = data["time"]
    day = datetime.fromisoformat(t["date"])
    band1 = _hm(day, t["elmfire_band1"])
    rounds = []
    for r in data["rounds"]:
        d, w = r["deployed"], r["weather"]
        rounds.append(
            Round(
                no=r["no"],
                version=r["version"],
                at=r["at"],
                report=r["report"],
                reader=r["reader"],
                deployed=Deployed(
                    helicopter=d.get("helicopter", {}),
                    crew=d.get("crew", {}),
                    vehicle=d.get("vehicle", {}),
                    reported_at=d.get("reported_at"),
                ),
                wind_dir_deg=w["wind_dir_deg"],
                wind_ms=w["wind_ms"],
                temp_c=w["temp_c"],
                rh_pct=w["rh_pct"],
                field_by_name={k: VillageField(**v) for k, v in r.get("villages", {}).items()},
                note=r.get("note", ""),
                day=day,
                band1=band1,
            )
        )
    rounds.sort(key=lambda x: x.no)
    ignition = (_hm(day, t["ignition_at"]) - band1).total_seconds()
    return Scenario(
        data.get("scenario", ""), data["incident"], rounds, ignition, data.get("sources", {})
    )


def load(input_dir: Path) -> Scenario:
    return parse(json.loads((Path(input_dir) / "scenario.json").read_text(encoding="utf-8")))
