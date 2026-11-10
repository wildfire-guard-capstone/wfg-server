# ruff: noqa: E501
"""2단계 규칙 판정: 지도 자료 + 현장 입력(scenario.json) + P0 ~ P8 → rules.Inputs → build_llm_input.

    out = run_rules(md, rd, sc, spread, deck_dir, prev_stage, prev_alerts)
    out["llm_input"] = {"bundles": 16묶음, "common": 공통, "items": 12개 항목}   # 규칙 엔진 출력 그대로

규칙 엔진(rules/, 황승준 담당)은 고치지 않는다. 에이전트는 이 출력에서 판정만 쓴다(agent/judgment.py).
"""

import math
import re
import time
from pathlib import Path

import numpy as np

from demo.uiseong.data import MapData, lonlat
from demo.uiseong.rounds import Round, Scenario
from demo.uiseong.step1_spread import SpreadResult
from rules.context import Inputs
from rules.core.stage import LABEL
from rules.llm_input import build_llm_input

MPH = 0.44704
STAGE_OF = {v: k for k, v in LABEL.items()}
MEAN_WIND = (
    "ws",
    "wd",
)  # 시간평균 풍속(mph) · 풍향 래스터 — 규칙 엔진의 「평균풍속」 기준에 맞는 값


def _namelist_file(text: str, key: str) -> str | None:
    m = re.search(rf"^\s*{key}\s*=\s*'([^']+)'", text, re.M)
    return m.group(1) if m else None


def wind_files(deck: Path) -> tuple[str, str, str]:
    """규칙 엔진에 넘길 바람 래스터(inputs/ 안 이름, 확장자 없음)와 출처 설명.

    덱 inputs/에 시간평균 ws · wd가 있으면 그것을 쓴다(11/1 덱). 없으면 ELMFIRE namelist의
    WS_FILENAME · WD_FILENAME을 쓴다 — 11/9 최종 덱은 ws_gust(시간 안 가장 센 1분 풍속)라
    평균풍속보다 크고, 대응단계의 풍속 기준 판정이 올라갈 수 있다(출처 설명에 남김).
    """
    ws, wd = MEAN_WIND
    if all((deck / "inputs" / f"{n}.tif").exists() for n in MEAN_WIND):
        return ws, wd, "시간평균 풍속(덱 inputs/ws · wd)"
    text = (deck / "elmfire.data").read_text(encoding="utf-8")
    ws = _namelist_file(text, "WS_FILENAME") or ws
    wd = _namelist_file(text, "WD_FILENAME") or wd
    note = f"ELMFIRE 입력 바람(덱 namelist {ws} · {wd})"
    if "gust" in ws:
        note += " — 시간평균이 아니라 돌풍 값이라 평균풍속보다 큼"
    return ws, wd, note


def wind_series(deck: Path, at_xy, t0_s: float, hours: int = 8) -> list[tuple[float, float]]:
    """ELMFIRE 입력의 시간별 풍속(mph) · 풍향을 한 점에서 읽어 t0 + h시간으로 선형 보간(u · v 성분).
    읽는 파일은 wind_files(deck)가 고른다."""
    import rasterio

    def band_vals(name):
        with rasterio.open(deck / "inputs" / f"{name}.tif") as r:
            row, col = r.index(*at_xy)
            return r.read()[:, row, col].astype(float)

    ws_name, wd_name, _ = wind_files(deck)
    ws, wd = band_vals(ws_name) * MPH, band_vals(wd_name)
    u = -ws * np.sin(np.radians(wd))
    v = -ws * np.cos(np.radians(wd))
    out = []
    for h in range(hours + 1):
        x = (t0_s + h * 3600) / 3600.0  # 밴드 1 = 0시간
        i = min(int(math.floor(x)), len(ws) - 2)
        f = x - i
        uu, vv = u[i] * (1 - f) + u[i + 1] * f, v[i] * (1 - f) + v[i + 1] * f
        out.append((math.hypot(uu, vv), (math.degrees(math.atan2(-uu, -vv)) + 360) % 360))
    return out


def make_inputs(
    md: MapData, rd: Round, spread: SpreadResult, deck: Path, prev_stage, prev_alerts
) -> tuple[Inputs, dict]:
    """규칙 엔진 입력. 0시 바람 = 상황 보고, 1 ~ 8시간 = ELMFIRE 입력 기상(화선 중심)."""
    observed = spread.observed
    winds = wind_series(deck, (observed.centroid.x, observed.centroid.y), rd.t_s)
    wind_ms = {0: rd.wind_ms, **{h: round(w[0], 1) for h, w in enumerate(winds) if h > 0}}
    wind_deg = {0: rd.wind_dir_deg, **{h: round(w[1]) for h, w in enumerate(winds) if h > 0}}
    field = {v.id: rd.field_by_name[v.name] for v in md.villages if v.name in rd.field_by_name}
    lon, lat = lonlat(md.ignition)
    inputs = Inputs(
        base_at=rd.base_at,
        generated_at=rd.base_at,
        reader=rd.reader,
        prev_stage=prev_stage,
        ranges=spread.ranges,
        ignition=md.ignition,
        wind_ms=wind_ms,
        wind_deg=wind_deg,
        houses=md.houses,
        sido=md.sido,
        sigungu=md.sigungu,
        villages=md.villages,
        site=(lat, lon),
        field=field,
        temp_c=rd.temp_c,
        rh_pct=rd.rh_pct,
        heritage=md.heritage,
        temples=(),  # 전통사찰 자료 없음(README 「내 기본값」)
        substations=md.substations,
        power_lines=md.power_lines,
        vulnerable=md.vulnerable,
        shelters=md.shelters,
        roads=md.roads,
        junctions=(),  # 고속도로 나들목 자료 없음
        police=md.police,
        fire_stations=md.fire_stations,
        hospitals=md.hospitals,
        bases=md.bases,
        deployed=rd.deployed,
        neighbors=md.neighbors,
        forest_dept={"의성군": "의성군 산림녹지과"},
        prev_alerts=prev_alerts,
    )
    summary = {
        "base_at": rd.base_at.isoformat(),
        "reader": rd.reader,
        "prev_stage": prev_stage,
        "wind_ms": wind_ms,
        "wind_deg": wind_deg,
        "wind_source": "0시간 = 상황 보고, 1 ~ 8시간 = " + wind_files(deck)[2],
        "temp_c": rd.temp_c,
        "rh_pct": rd.rh_pct,
        "field": {rd_name: vars(f) for rd_name, f in rd.field_by_name.items()},
        "deployed": {
            "helicopter": dict(rd.deployed.helicopter),
            "crew": dict(rd.deployed.crew),
            "vehicle": dict(rd.deployed.vehicle),
            "reported_at": rd.deployed.reported_at,
        },
        "prev_alerts": [list(a) for a in prev_alerts],
        "areas_ha": spread.areas_ha,
    }
    return inputs, summary


def run_rules(
    md: MapData, rd: Round, sc: Scenario, spread: SpreadResult, deck: Path, prev_stage, prev_alerts
) -> dict:
    inputs, summary = make_inputs(md, rd, spread, deck, prev_stage, prev_alerts)
    t0 = time.time()
    llm_in = build_llm_input(inputs, sc.incident)
    llm_in["common"]["proposal"] |= {
        "version": rd.version,
        "trigger": "예측 갱신",
        "base_perimeter": f"{rd.report} ({rd.at}, 상황도)",
    }
    b = llm_in["bundles"]
    return {
        "inputs": summary,
        "llm_input": llm_in,
        "seconds": round(time.time() - t0, 2),
        "stage_now": b["stage"]["now"]["stage"],
        "stage_p5": b["stage"]["p5"]["stage"],
        "commander": f"{b['commander']['now']['title']} → {b['commander']['next']['title']}",
        "next_prev_stage": STAGE_OF[b["stage"]["now"]["stage"]],
        "next_prev_alerts": tuple(
            (r["sigungu"], r["emd"], r["level"]) for r in b["alerts"]["areas"]
        ),
        "weather": {
            "wind_ms": summary["wind_ms"],
            "wind_deg": summary["wind_deg"],
            "wind_source": summary["wind_source"],
        },
    }
