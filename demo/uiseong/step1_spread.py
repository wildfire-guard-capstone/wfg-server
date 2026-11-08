# ruff: noqa: E501
"""1단계 확산 예측: 회차 k보 화선에서 ELMFIRE 보정 · 8시간 예측 → P0 ~ P8 → 뒤 상황도로 검증.

    res = run_spread(deck, work, md, rd, sc, method)   # ELMFIRE 실행(GPU 서버, CPU로 돈다)
    save(res, folder) / load(folder)                    # 01_spread/spread.json (다음 실행에서 --reuse-spread)

보정(11/7 · 11/8 사용자 결정): 확산속도 배율(adj) 하나 = 두 배율의 기하평균(blend)
  ① k−1보 화선 → k보 화선 면적 맞춤 ② k보 화선에서 발화 뒤 평균 성장 속도를 1시간 잇기. 1회차는 ②만.
"""

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from shapely import wkt
from shapely.geometry import mapping
from shapely.ops import unary_union

from demo.uiseong.data import MapData, to4326
from demo.uiseong.rounds import Round, Scenario
from spread import perimeter_run as pr

CAL_METHODS = {
    "blend": "직전 구간 배율과 발화 뒤 평균 속도 배율의 기하평균(11/8 사용자 결정, 기본)",
    "interval": "직전 구간(k−1보 화선 → k보 화선) 면적 맞춤(11/7)",
    "rate": "발화 뒤 평균 성장 속도 유지(k보 화선 + 1시간 = 면적 ÷ 발화 뒤 시간)",
    "from_first": "1보 화선 → k보 화선 면적 맞춤",
}


@dataclass
class SpreadResult:
    calibration: dict
    ranges: list  # shapely, EPSG:5179 (P0 = 관측 화선)
    observed: object
    areas_ha: list[float]
    validation: list[dict]
    wall_s: float = 0.0
    reused_from: str | None = None
    extra: dict = field(default_factory=dict)


def _part(cal: pr.Calibration, how: str) -> dict:
    return {
        "from": how,
        "adj": cal.adj,
        "target_ha": round(cal.target_ha, 1),
        "got_ha": round(cal.got_ha, 1),
        "tries": [(round(k, 3), round(v, 1)) for k, v in cal.tries],
        "clipped": cal.clipped,
    }


def calibrate_round(
    deck: pr.Deck, work: Path, md: MapData, rd: Round, sc: Scenario, method: str = "blend"
) -> dict:
    """회차 rd의 확산속도 배율과 그 근거(round_k.json의 calibration).

    1회차는 어느 방법이든 rate(11/7 사용자 결정 — 발화점에서 출발하면 점 출발 가속 구간 때문에
    배율이 폭주한다). blend = interval과 rate의 기하평균(11/8): 직전 구간 하나로 맞춘 배율은
    손으로 그린 화선 차이와 급성장 구간에 따라 2.75 ~ 7.0으로 흔들렸고(14:20 예측이 22:00에
    실측의 3.6배), 발화 뒤 평균만 쓰면 13:10 · 13:45 예측이 실측의 0.6배로 작았다(backtest.py).
    """
    if method not in CAL_METHODS:
        raise ValueError(f"보정 방법 {method}: {list(CAL_METHODS)} 중 하나")
    observed = md.report(rd.at)
    by_no = {r.no: r for r in sc.rounds}

    def rate_part() -> dict:
        # k보 화선에서 1시간 동안 늘어나는 면적 = 발화 ~ k보 평균 속도. 그 시각에 아는 정보만
        rate = observed.area / 1e4 / ((rd.t_s - sc.ignition_t_s) / 3600)
        target = observed.area / 1e4 + rate
        cal = pr.calibrate(
            deck,
            work / f"cal_{rd.no}_rate",
            observed,
            rd.t_s,
            rd.t_s + 3600,
            None,
            target_ha=target,
        )
        return _part(
            cal, f"발화 뒤 평균 성장 속도 유지(시간당 {rate:.1f} ha, {rd.report} 화선 + 1시간)"
        )

    def span_part(before: Round) -> dict:
        cal = pr.calibrate(
            deck,
            work / f"cal_{rd.no}_from{before.no}",
            md.report(before.at),
            before.t_s,
            rd.t_s,
            observed,
        )
        return _part(cal, f"{before.report} {before.at} → {rd.report} {rd.at} 화선")

    first = min(by_no)
    if rd.no == first or method == "rate":
        method, parts = "rate", [rate_part()]
    elif method == "interval":
        parts = [span_part(by_no[rd.no - 1])]
    elif method == "from_first":
        parts = [span_part(by_no[first])]
    else:
        parts = [span_part(by_no[rd.no - 1]), rate_part()]
    adj = math.exp(sum(math.log(p["adj"]) for p in parts) / len(parts))
    how = (
        parts[0]["from"] if len(parts) == 1 else "두 배율의 기하평균(직전 구간 · 발화 뒤 평균 속도)"
    )
    return {"method": method, "adj": adj, "from": how, "parts": parts}


def validate(md: MapData, rd: Round, observed, res: pr.RunResult, hours: float = 8) -> list[dict]:
    """예측을 뒤 상황도 화선과 비교(면적 · IoU). 둘 다 기준 화선과 합친 범위로 잰다(제안에는 쓰지 않음)."""
    checks = []
    for later in md.perimeters["reports"] + md.perimeters["later"]:
        h, m = map(int, later["at"].split(":"))
        dt_h = (h * 60 + m - (rd.base_at.hour * 60 + rd.base_at.minute)) / 60
        if 0 < dt_h <= hours:
            pred = pr.polygonize(res.burned_by(rd.t_s + dt_h * 3600), res.transform)
            pred = unary_union([pred, observed])
            obs = unary_union([md.report(later["at"]), observed])
            inter = pred.intersection(obs).area
            checks.append(
                {
                    "at": later["at"],
                    "hours": round(dt_h, 2),
                    "pred_ha": round(pred.area / 1e4, 1),
                    "obs_ha": round(obs.area / 1e4, 1),
                    "iou": round(inter / pred.union(obs).area, 3) if pred.area else 0,
                }
            )
    return checks


def run_spread(
    deck: pr.Deck, work: Path, md: MapData, rd: Round, sc: Scenario, method: str = "blend"
) -> SpreadResult:
    observed = md.report(rd.at)
    t0 = time.time()
    cal = calibrate_round(deck, work, md, rd, sc, method)
    res = pr.run(deck, work / f"fcst_{rd.no}", rd.t_s, 8, observed, cal["adj"])
    rngs = pr.ranges(res, observed)
    return SpreadResult(
        calibration=cal,
        ranges=rngs,
        observed=observed,
        areas_ha=[round(g.area / 1e4, 1) for g in rngs],
        validation=validate(md, rd, observed, res),
        wall_s=round(time.time() - t0, 1),
        extra={"elmfire_s": round(res.wall_s, 1)},
    )


def save(r: SpreadResult, folder: Path) -> None:
    """01_spread/: spread.json(다시 쓰기용 EPSG:5179 WKT 포함) · ranges_4326.geojson(지도 확인용)."""
    folder.mkdir(parents=True, exist_ok=True)
    out = {
        "calibration": r.calibration,
        "areas_ha": r.areas_ha,
        "validation": r.validation,
        "wall_s": r.wall_s,
        "reused_from": r.reused_from,
        "ranges_wkt_5179": [g.wkt for g in r.ranges],
        "observed_wkt_5179": r.observed.wkt,
        **r.extra,
    }
    (folder / "spread.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"range": f"P{i}", "area_ha": a},
                "geometry": mapping(to4326(g)),
            }
            for i, (g, a) in enumerate(zip(r.ranges, r.areas_ha, strict=True))
        ],
    }
    (folder / "ranges_4326.geojson").write_text(
        json.dumps(fc, ensure_ascii=False), encoding="utf-8"
    )


def load(folder: Path) -> SpreadResult:
    """앞 실행의 01_spread/spread.json을 읽는다(ELMFIRE를 다시 돌리지 않고 규칙 · 제안만 다시 볼 때)."""
    d = json.loads((folder / "spread.json").read_text(encoding="utf-8"))
    return SpreadResult(
        calibration=d["calibration"],
        ranges=[wkt.loads(s) for s in d["ranges_wkt_5179"]],
        observed=wkt.loads(d["observed_wkt_5179"]),
        areas_ha=d["areas_ha"],
        validation=d["validation"],
        wall_s=0.0,
        reused_from=str(folder),
    )
