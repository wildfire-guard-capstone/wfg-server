# ruff: noqa: E501
"""1b단계 위험도: 확산 예측 P5 + 현재 화선 → 23개 변수 → R(1.00 ~ 5.00). 계산은 `risk/`, 여기는 의성 자료 어댑터.

    data = RiskData.load(input_dir)                 # 지역 격자가 없으면 만들어 <input>/risk/grid에 둔다(약 1분)
    out = run_risk(data, md, rd, spread)            # round_k/01b_risk/risk.json 내용
    python -m demo.uiseong.step1b_risk --input <demo-uiseong/input> --runs <결과 폴더> [--mode mean_0_5|at_p5]
        → 앞 결과의 round_k/01_spread로 위험도만 계산해 round_k/01b_risk/에 쓴다(ELMFIRE · LLM 없이 로컬에서 돈다)

자료(README 「위험도」, 설계 결정은 risk/README.md):
- 지형 · 연료 13개 = 지역 격자(`risk.grid`): ELMFIRE 입력(dem · fbfm · cc · ch, 30 m) + 지도 17_저수지_호수
- 풍속 · 연료 수분 = ELMFIRE 입력 ws · m1(600 m, 시간별, 확산 예측과 같은 기상)
- 기온 · 습도 · 강수 · 7일 누적 강수 = ASOS 의성(278) 관측, 해면기압 · 지면온도 = Open-Meteo 과거 예보 보관
  (<input>/risk/weather_hourly.csv, prepare_risk_data.py)
- 취약계층 = 마을(리) 인구 × 읍면 (65세 이상 + 0 ~ 14세) 비율(SGIS 2024), 의성 밖은 읍면동 인구 × 그 비율
- 소방서 = 지도 09_소방서_119안전센터(소방서 · 119안전센터 · 119지역대)
"""

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from demo.uiseong.data import MapData, _feats
from demo.uiseong.rounds import Round
from demo.uiseong.step1_spread import SpreadResult
from risk import fuel as fuel_mod
from risk import grid as grid_mod
from risk.compute import RiskInputs, compute_risk
from risk.criteria import BY_KEY
from risk.overlap import Grid
from risk.weather import HOURS, RAIN_HOURS, GridField

MPH = 0.44704
SOURCES = {
    "elevation": "Copernicus DEM 30 m(ELMFIRE 입력 dem)",
    "slope": "DEM · Horn 3×3",
    "aspect": "DEM · Horn 3×3, 경사 0.5° 미만 = 평지(3등급)",
    "curvature": "DEM · Zevenbergen & Thorne 전곡률 × 100",
    "tpi": "DEM · 반경 150 m 평균과의 차",
    "complexity": "DEM · 7×7칸(210 m) 창 최고 − 최저",
    "water_dist": "DEM 흐름 누적 0.5 km² 이상 물길 + OSM 저수지 · 호수",
    "fuel_type": "ELMFIRE 입력 연료 모델(임상도 2024 · 토지피복)",
    "fuel_load": "연구자료 제1036호 연소가능 수관연료량 + 지표 연료(ELMFIRE 입력 연료 모델)",
    "fuel_depth": "ELMFIRE 연료 사전 연료층 깊이(숲 0.3 ft = 가정값)",
    "stand_age": "임상도 영급 → 10n − 5년(ELMFIRE 입력 연료 모델)",
    "canopy_height": "ELMFIRE 입력 ch(임상도 임분고)",
    "canopy_cover": "ELMFIRE 입력 cc(임상도 밀도 소 30 · 중 55 · 밀 85%)",
    "wind": "ELMFIRE 입력 ws(ASOS · AWS 5곳 IDW, 600 m)",
    "moisture": "ELMFIRE 입력 m1(1시간 고사연료, 600 m)",
    "temp": "ASOS 의성(278) 관측",
    "humidity": "ASOS 의성(278) 관측",
    "precip": "ASOS 의성(278) 관측",
    "rain_7d": "ASOS 의성(278) 관측",
    "pressure": "Open-Meteo 과거 예보 보관 해면기압(의성 관측소 위치)",
    "ground_temp": "Open-Meteo 과거 예보 보관 지면(0 cm) 온도(의성 관측소 위치)",
    "vulnerable": "리 인구(주민등록 2026-09) × 읍면 (65세 이상 + 0~14세) 비율(SGIS 2024)",
    "station_dist": "소방청 소방서 좌표 + 119지역대(지도 09)",
}


def _read_band_stack(path: Path):
    import rasterio

    with rasterio.open(path) as r:
        t = r.transform
        return r.read().astype("float64"), Grid(t.c, t.f, t.a, r.height, r.width)


def build_grid(input_dir: Path, deck: Path, out: Path) -> Path:
    """ELMFIRE 입력 30 m 격자 + 지도 17_저수지_호수 → 지역 격자(risk.grid) 파일."""
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    (dem,), g = _read_band_stack(deck / "inputs" / "dem.tif")
    (fb,), _ = _read_band_stack(deck / "inputs" / "fbfm.tif")
    (cc,), _ = _read_band_stack(deck / "inputs" / "cc.tif")
    (ch,), _ = _read_band_stack(deck / "inputs" / "ch.tif")
    water_path = next((input_dir / "map").glob("17_*.geojson"))
    polys = [
        geom for _, geom in _feats(water_path) if geom.geom_type in ("Polygon", "MultiPolygon")
    ]
    water = rasterize(
        ((p, 1) for p in polys),
        out_shape=(g.h, g.w),
        transform=from_origin(g.x0, g.y0, g.cs, g.cs),
        fill=0,
        dtype="uint8",
    ).astype(bool)
    sg = grid_mod.build_static_grid(
        dem, fb.astype(int), cc, ch / 10.0, g, fuel_mod.load_fuel_models(deck), water,
        meta={
            "region": "의성(ELMFIRE 입력 11/1 영역)",
            "sources": {"deck": str(deck.name), "water": water_path.name, "water_polygons": len(polys)},
            "built": datetime.now().isoformat(timespec="seconds"),
        },
    )  # fmt: skip
    return grid_mod.save(sg, out)


@dataclass
class RiskData:
    static: grid_mod.StaticGrid
    ws: np.ndarray  # (밴드, h, w) mph
    m1: np.ndarray  # (밴드, h, w) %
    wgrid: Grid
    band1: datetime
    hourly: dict[datetime, dict]
    ratios: dict[str, dict]

    @classmethod
    def load(
        cls, input_dir: Path, deck: Path | None = None, grid_dir: Path | None = None, band1=None
    ):
        input_dir = Path(input_dir)
        deck = deck or input_dir / "elmfire_deck"
        grid_dir = grid_dir or input_dir / "risk" / "grid"
        if not (grid_dir / "meta.json").exists():
            print(f"  위험도 지역 격자 만들기 → {grid_dir}", flush=True)
            build_grid(input_dir, deck, grid_dir)
        ws, wgrid = _read_band_stack(deck / "inputs" / "ws.tif")
        m1, _ = _read_band_stack(deck / "inputs" / "m1.tif")
        hourly = {}
        with open(input_dir / "risk" / "weather_hourly.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                hourly[datetime.fromisoformat(r["time"])] = {
                    k: (float(v) if v not in ("", None) else None)
                    for k, v in r.items()
                    if k != "time"
                }
        ratios = json.loads(
            (input_dir / "risk" / "vulnerable_ratio.json").read_text(encoding="utf-8")
        )
        if band1 is None:
            sc = json.loads((input_dir / "scenario.json").read_text(encoding="utf-8"))["time"]
            band1 = datetime.fromisoformat(f"{sc['date']}T{sc['elmfire_band1']}")
        return cls(grid_mod.load(grid_dir), ws, m1, wgrid, band1, hourly, ratios)

    # ── 시각별 값 ──
    def _band(self, stack: np.ndarray, at: datetime, scale: float = 1.0) -> GridField:
        x = (at - self.band1).total_seconds() / 3600.0  # 밴드 1 = 0
        i = min(max(int(np.floor(x)), 0), stack.shape[0] - 2)
        f = min(max(x - i, 0.0), 1.0)
        return GridField((stack[i] * (1 - f) + stack[i + 1] * f) * scale, self.wgrid)

    def _point(self, key: str, at: datetime) -> float | None:
        t0 = at.replace(minute=0, second=0, microsecond=0)
        a, b = (
            self.hourly.get(t0, {}).get(key),
            self.hourly.get(t0 + timedelta(hours=1), {}).get(key),
        )
        f = (at - t0).total_seconds() / 3600.0
        if f == 0:
            return a
        if a is None or b is None:
            return None
        return a * (1 - f) + b * f

    def _rain(self, after: datetime, until: datetime) -> float | None:
        """(after, until]에 정시 기록이 있는 1시간 강수의 합. 기록이 빠진 시각이 있으면 None."""
        t = after.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        total, n = 0.0, 0
        while t <= until:
            row = self.hourly.get(t)
            if row is None or row.get("precip_mm") is None:
                return None
            total += row["precip_mm"]
            n += 1
            t += timedelta(hours=1)
        return total if n else None

    def weather(self, base_at: datetime) -> tuple[dict, float | None, dict]:
        """(변수 → 시간 → 값, 7일 누적 강수, 화면용 시각별 한 점 값)."""
        w: dict[str, dict] = {
            k: {}
            for k in ("temp", "humidity", "pressure", "ground_temp", "precip", "wind", "moisture")
        }
        shown = []
        for h in HOURS:
            at = base_at + timedelta(hours=h)
            w["temp"][h] = self._point("temp_c", at)
            w["humidity"][h] = self._point("rh_pct", at)
            w["pressure"][h] = self._point("pressure_msl_hpa", at)
            w["ground_temp"][h] = self._point("ground_temp_c", at)
            w["wind"][h] = self._band(self.ws, at, MPH)
            w["moisture"][h] = self._band(self.m1, at)
            shown.append(
                {
                    "h": h,
                    "at": at.strftime("%H:%M"),
                    **{k: w[k][h] for k in ("temp", "humidity", "pressure", "ground_temp")},
                }
            )
        for h in RAIN_HOURS:
            w["precip"][h] = self._rain(
                base_at + timedelta(hours=h - 1), base_at + timedelta(hours=h)
            )
        rain_7d = self._rain(base_at - timedelta(days=7), base_at)
        return w, rain_7d, {"hours": shown, "precip": w["precip"], "rain_7d": rain_7d}

    def zones(self, md: MapData) -> tuple[list, dict]:
        """취약계층 구역: (경계, 65세 이상 + 0 ~ 14세 추정). 의성 리 = 리 인구 × 그 읍면 비율."""
        uiseong = {v["name"]: c for c, v in self.ratios.items() if c.startswith("37520")}
        out, miss = [], []
        for v in md.villages:
            if v.id in md.township_ids:
                r = self.ratios.get(str(v.id))
            else:
                r = self.ratios.get(uiseong.get(v.emd, ""))
            if r is None or r["ratio"] is None:
                miss.append(v.name)
                continue
            out.append((v.geom, v.population * r["ratio"]))
        return out, {"zones": len(out), "no_ratio": miss}


def run_risk(
    data: RiskData, md: MapData, rd: Round, spread: SpreadResult, mode: str = "mean_0_5"
) -> dict:
    weather, rain_7d, shown = data.weather(rd.base_at)
    zones, zinfo = data.zones(md)
    inp = RiskInputs(
        p5=spread.ranges[5],
        perimeter=spread.observed,
        static=data.static,
        weather=weather,
        rain_7d=rain_7d,
        zones=zones,
        stations=[p.geom for p in md.fire_stations],
        mode=mode,
        sources=SOURCES,
    )
    res = compute_risk(inp)
    return {
        **res.to_dict(),
        "base_at": rd.base_at.isoformat(),
        "p5_until": (rd.base_at + timedelta(hours=5)).strftime("%H:%M"),
        "weather_used": shown,
        "zones": zinfo,
        "grid": {k: data.static.meta.get(k) for k in ("grid", "parameters", "region")},
    }


def table(r: dict) -> str:
    """사람이 읽는 표(risk.md)."""
    lines = [
        f"# 위험도 R = {r['score']:.2f}  (P5 {r['p5_ha']:.1f} ha, ~{r['p5_until']}, 기상 {r['mode']})",
        "",
        "| 축 | 변수 | 값 | 등급 | 가중치(CSV → 다시 나눔) | 기여 | 설명 · 자료 |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in r["factors"]:
        v = "" if f["value"] is None else f"{f['value']:,.2f} {f['unit']}".strip()
        g = "—" if f["grade"] is None else f"{f['grade']:.2f}"
        lines.append(
            f"| {f['axis']} | {f['name']} | {v} | {g} | {f['weight_csv']:.2f} → {f['weight']:.3f} | "
            f"{f['contribution']:.3f} | {f['detail']} · {f['source']} |"
        )
    lines += ["", "| 축 | 등급(가중평균) | 가중치 합 | 기여 |", "|---|---|---|---|"]
    for ax, a in r["axes"].items():
        g = "—" if a["grade"] is None else f"{a['grade']:.2f}"
        lines.append(f"| {ax} {a['name']} | {g} | {a['weight']:.3f} | {a['contribution']:.3f} |")
    if r["missing"]:
        lines += ["", "뺀 변수: " + ", ".join(BY_KEY[k].name for k in r["missing"])]
    for n in r.get("notes", []):
        lines.append(f"- {n}")
    return "\n".join(lines) + "\n"


def save(r: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "risk.json").write_text(
        json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    (folder / "risk.md").write_text(table(r), encoding="utf-8")


def main() -> None:
    from demo.uiseong import rounds as rounds_mod
    from demo.uiseong import step1_spread

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--runs", type=Path, required=True, help="round_k/01_spread가 있는 결과 폴더")
    ap.add_argument("--grid", type=Path, help="지역 격자 폴더(기본 <input>/risk/grid, 없으면 만듦)")
    ap.add_argument("--mode", default="mean_0_5", choices=["mean_0_5", "at_p5"])
    a = ap.parse_args()
    sc = rounds_mod.load(a.input)
    md = MapData(a.input)
    data = RiskData.load(a.input, grid_dir=a.grid)
    for rd in sc.rounds:
        sp_dir = a.runs / f"round_{rd.no}" / "01_spread"
        if not (sp_dir / "spread.json").exists():
            continue
        r = run_risk(data, md, rd, step1_spread.load(sp_dir), a.mode)
        save(r, a.runs / f"round_{rd.no}" / "01b_risk")
        print(
            f"회차 {rd.no} {rd.at}: R {r['score']:.2f} · P5 {r['p5_ha']:.0f} ha · 뺀 변수 {r['missing']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
