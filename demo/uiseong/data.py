"""데모 어댑터: 의성 데모 데이터 폴더 → 규칙 엔진 `Inputs` 재료(파일 기반, DB 어댑터 대용).

app의 DB → Inputs 어댑터가 아직 없어(rules/README 8장) 같은 모양을 파일에서 만든다.
좌표는 모두 EPSG:5179. 지도 자료는 `prepare_data.py`가 만든 폴더를 읽는다.

자료와 내 기본값(데모용, 근거 · 출처는 README):
- 마을 = 03_마을_리(법정리 182곳, 주민등록 인구 2026-09). id = 법정동코드
  의성 밖(리 자료 없음) = SGIS 행정동 하나를 「○○면 전체」 마을 하나로(인구 SGIS 2024).
  읍면 칸에는 시군 이름
- 주택 = 05_주택(단독 · 공동주택 건물 중심점 23,160동)
- 취약시설 = 07_취약시설 34곳. 주야간보호만 daytime_only
- 대피소 = 06_이재민_임시주거시설 78곳. 경로당 → village, 숙박시설(모텔) → 후보 아님,
  그 밖(학교 · 복지 · 공공) → hub. 같은 이름은 작은 수용 인원 하나만
- 국가유산 = 14_국가유산 46곳(지정 종류 그대로).
  전통사찰 자료는 없어 비운다(LLM-입력-정리 「남은 확인」의 대체안)
- 변전소 · 발전소 = 16_송전철탑_변전소 중 substation · plant, 송전선 = 15_송전선(154 · 345 kV)
- 도로 = 18_도로 중 큰 도로(cls major). 노선 번호 · 나들목 자료 없음
- 경찰 = 13(이름으로 종류), 소방 = 09(type으로 종류), 병원 = 12(모두 응급실 있음)
- 헬기 기지 = 10(시연값). 부를 수 있는 대수 · 기지별 투입은 자료 없음(None).
  관할은 아래 COVERAGE(가정)
"""

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform as sh_transform
from shapely.strtree import STRtree

from rules.core.places import Base, Place, PowerLine, RoadPiece
from rules.core.regions import Region
from rules.core.villages import Village

_TF = Transformer.from_crs(4326, 5179, always_xy=True)
_TB = Transformer.from_crs(5179, 4326, always_xy=True)
HOME = "의성군"
HOME_SIDO = "경상북도"
FOREST_DEPT = {"의성군": "의성군 산림녹지과"}  # 규칙 예시(12:50)와 같은 이름
# 헬기 기지 관할(가정): 지자체 임차헬기 = 그 시군, 소방 = 시 · 도,
# 산림청 관리소는 가까운 관리소가 관할
COVERAGE = {
    "안동산림항공관리소": ["의성군", "안동시"],
    "울진산림항공관리소": ["울진군"],
    "양산산림항공관리소": ["경상남도"],
    "경북 119 항공대": ["경상북도"],
}
BASE_KIND = {"forest": "산림청 헬기", "local_lease": "지자체 임차헬기", "fire": "소방 헬기"}
FIRE_KIND = {"FIRE_STATION": "소방서", "SAFETY_CENTER": "119안전센터", "LOCAL_UNIT": "119지역대"}


def to5179(geom):
    return sh_transform(lambda x, y, z=None: _TF.transform(x, y), geom)


def to4326(geom):
    return sh_transform(lambda x, y, z=None: _TB.transform(x, y), geom)


def lonlat(pt) -> tuple[float, float]:
    return _TB.transform(pt.x, pt.y)


def _feats(path: Path):
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        yield f["properties"], to5179(shape(f["geometry"]))


@dataclass
class MapData:
    folder: Path

    def _layer(self, prefix: str):
        path = next((self.folder / "map").glob(f"{prefix}_*.geojson"))
        return list(_feats(path))

    # ── 경계 ──
    @cached_property
    def _regions(self) -> dict:
        return json.loads((self.folder / "regions.json").read_text(encoding="utf-8"))

    @cached_property
    def sido(self) -> list[Region]:
        return [Region(r["name"], shape(r["geom"])) for r in self._regions["sido"]]

    @cached_property
    def sigungu(self) -> list[Region]:
        return [Region(r["name"], shape(r["geom"])) for r in self._regions["sigungu"]]

    @cached_property
    def _sg_rows(self) -> list[tuple[dict, object]]:
        return [(r, shape(r["geom"])) for r in self._regions["sigungu"]]

    def place_at(self, pt) -> tuple[str, str]:
        """점이 든 시 · 군 · 구와 시 · 도.

        이름이 같은 구(동구 · 중구)가 여러 시에 있어 경계 행으로 찾는다.
        """
        hit = next((r for r, g in self._sg_rows if g.covers(pt)), None)
        if hit is None:
            hit = min(self._sg_rows, key=lambda t: t[1].distance(pt))[0]
        return hit["name"], hit["sido"]

    def sigungu_at(self, pt) -> str:
        return self.place_at(pt)[0]

    @cached_property
    def neighbors(self) -> dict[str, list[str]]:
        home = next(r for r in self.sigungu if r.name == HOME)
        ring = home.geom.buffer(60)  # 단순화(20 m)로 생긴 틈
        names = sorted({r.name for r in self.sigungu if r.name != HOME and r.geom.intersects(ring)})
        return {HOME: names}

    # ── 마을 · 주택 ──
    @cached_property
    def villages(self) -> list[Village]:
        """의성 = 법정리. 의성 밖 = 읍면동 하나를 마을 하나로(「일직면 전체」, 읍면 칸 = 시군 이름).

        의성 밖은 리 자료가 없어 엔진이 멈추므로 데모에서만 읍면 단위로 대신 넣는다
        (11/7 사용자 결정).
        """
        out = []
        for p, g in self._layer("03"):
            out.append(
                Village(
                    int(p["code"]), p["ri"], p["emd"], HOME, g.buffer(0), int(p["pop"] or 0), None
                )
            )
        for t in self._regions.get("townships", []):
            out.append(
                Village(
                    int(t["code"]),
                    f"{t['name']} 전체",
                    t["sigungu"],
                    t["sigungu"],
                    shape(t["geom"]).buffer(0),
                    int(t["pop"] or 0),
                    None,
                )
            )
        return out

    @cached_property
    def township_ids(self) -> set[int]:
        return {int(t["code"]) for t in self._regions.get("townships", [])}

    @cached_property
    def _village_tree(self):
        return STRtree([v.geom for v in self.villages])

    def village_id_at(self, pt) -> int | None:
        for i in self._village_tree.query(pt, predicate="intersects"):
            return self.villages[int(i)].id
        return None

    @cached_property
    def houses(self) -> tuple[list[float], list[float]]:
        xs, ys = [], []
        for _, g in self._layer("05"):
            xs.append(g.x)
            ys.append(g.y)
        return xs, ys

    # ── 시설 ──
    @cached_property
    def vulnerable(self) -> list[Place]:
        out = []
        for i, (p, g) in enumerate(self._layer("07"), 1):
            kind = p.get("group")
            out.append(
                Place(
                    1000 + i,
                    p["name"],
                    kind,
                    g,
                    village_id=self.village_id_at(g),
                    sigungu=HOME,
                    daytime_only="주야간" in (kind or ""),
                    capacity=p.get("cap"),
                )
            )
        return out

    @cached_property
    def shelters(self) -> list[Place]:
        best: dict[str, tuple] = {}
        for p, g in self._layer("06"):
            cap = int(p["cap"]) if p.get("cap") not in (None, "") else None
            name = p["name"]
            if name not in best or (cap or 1e9) < (best[name][0]["cap"] or 1e9):
                best[name] = ({**p, "cap": cap}, g)
        out = []
        for i, (p, g) in enumerate(best.values(), 1):
            # 경로당 = 먼저 모일 곳, 숙박시설(모텔) = 후보 아님(rules Place.tier), 그 밖 = 머물 곳
            tier = {"경로당": "village", "숙박시설": None}.get(p["kind"], "hub")
            out.append(
                Place(
                    2000 + i,
                    p["name"],
                    p["kind"],
                    g,
                    village_id=self.village_id_at(g),
                    sigungu=HOME,
                    capacity=p["cap"],
                    tier=tier,
                )
            )
        return out

    @cached_property
    def heritage(self) -> list[Place]:
        return [
            Place(3000 + i, p["name"], p["kind"], g, village_id=self.village_id_at(g), sigungu=HOME)
            for i, (p, g) in enumerate(self._layer("14"), 1)
        ]

    @cached_property
    def substations(self) -> list[Place]:
        names = {"substation": "변전소", "plant": "발전소"}
        return [
            Place(
                4000 + i,
                p.get("name") or names[p["kind"]],
                names[p["kind"]],
                g,
                village_id=self.village_id_at(g),
                sigungu=self.sigungu_at(g),
            )
            for i, (p, g) in enumerate(self._layer("16"), 1)
            if p["kind"] in names
        ]

    @cached_property
    def power_lines(self) -> list[PowerLine]:
        return [
            PowerLine(i, int(int(p["voltage"]) / 1000), g)
            for i, (p, g) in enumerate(self._layer("15"), 1)
        ]

    @cached_property
    def roads(self) -> list[RoadPiece]:
        return [
            RoadPiece(i, p["hw"], p.get("name") or None, None, g)
            for i, (p, g) in enumerate(self._layer("18"), 1)
            if p.get("cls") == "major"
        ]

    @cached_property
    def police(self) -> list[Place]:
        def kind(n: str) -> str:
            for k in ("지구대", "파출소", "치안센터"):
                if k in n:
                    return k
            return "경찰서" if n.endswith("경찰서") else "기타"

        return [
            Place(5000 + i, p["name"], kind(p["name"]), g, sigungu=self.sigungu_at(g))
            for i, (p, g) in enumerate(self._layer("13"), 1)
        ]

    @cached_property
    def fire_stations(self) -> list[Place]:
        out = []
        for i, (p, g) in enumerate(self._layer("09"), 1):
            kind = FIRE_KIND.get(p.get("type"), p.get("type"))
            parts = [s.strip() for s in p["name"].split("-")]
            name = (
                parts[0]
                if kind == "소방서" or len(parts) < 3
                else f"{parts[0]} {parts[1]}{parts[2].replace(' ', '')}"
            )
            out.append(Place(6000 + i, name, kind, g, sigungu=self.sigungu_at(g)))
        return out

    @cached_property
    def hospitals(self) -> list[Place]:
        return [
            Place(7000 + i, p["name"], p.get("kind"), g, sigungu=self.sigungu_at(g), emergency=True)
            for i, (p, g) in enumerate(self._layer("12"), 1)
        ]

    @cached_property
    def bases(self) -> list[Base]:
        out = []
        for i, (p, g) in enumerate(self._layer("10"), 1):
            sg, sido = self.place_at(g)
            cov = COVERAGE.get(p["name"]) or ([sg] if p["org"] == "local_lease" else [])
            out.append(Base(i, p["name"], BASE_KIND[p["org"]], sg, sido, cov, g))
        return out

    # ── 화선 ──
    @cached_property
    def perimeters(self) -> dict:
        return json.loads((self.folder / "perimeters.json").read_text(encoding="utf-8"))

    def report(self, at: str):
        for r in self.perimeters["reports"] + self.perimeters["later"]:
            if r["at"] == at:
                return shape(r["geom"]).buffer(0)
        raise KeyError(at)

    @cached_property
    def ignition(self) -> Point:
        ig = self.perimeters["ignition"]
        return Point(ig["x"], ig["y"])
