# ruff: noqa: E501
"""위험도 지역 격자(지형 7 · 연료 6) 만들기 — ELMFIRE 덱을 시군구 범위로 잘라 risk.grid 파일로.

    python -m demo.uiseong.build_risk_grid --input <demo-uiseong/input> --deck <elmfire_deck> \\
        --out <폴더> [--sigungu 의성군] [--margin-m 2000] [--tables <대응표가 있는 덱>]

자료는 새로 모으지 않는다 — 모두 확산 예측에 쓰는 ELMFIRE 덱(dem · fbfm · cc · ch · fuel_models.csv)과
지도 레이어 17_저수지_호수(수계)다. 범위 = <input>/regions.json의 시군구 경계 상자 + margin(경계 근처
산불의 P5가 군 밖으로 조금 나가도 덮게).

--tables: 연료 대응표(inputs/fuel_model_mapping.csv · scripts/fuel_table_raw.csv)가 덱에 없을 때 쓸 덱.
11/10 A′ 릴리스 덱에는 없다 — 모델 번호 · 이름 · 연료량이 11/1 덱과 같아 그 덱의 표를 쓴다.

API(EC2)는 설정 RISK_GRID_DIR로 이 폴더를 가리키고, 요청마다 P5 근처 칸만 읽는다(risk.grid.load).
"""

import argparse
import json
import time
from pathlib import Path

from shapely.geometry import shape

from demo.uiseong.step1b_risk import build_grid


def sigungu_bounds(input_dir: Path, name: str, margin_m: float) -> tuple[float, ...]:
    """regions.json 시군구 경계(EPSG:5179) 상자 + margin."""
    regions = json.loads((input_dir / "regions.json").read_text(encoding="utf-8"))
    hit = [r for r in regions["sigungu"] if r["name"] == name]
    if not hit:
        names = ", ".join(r["name"] for r in regions["sigungu"])
        raise SystemExit(f"regions.json에 {name}이 없음(있는 것: {names})")
    x0, y0, x1, y1 = shape(hit[0]["geom"]).bounds
    return x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--input", type=Path, required=True, help="demo-uiseong/input(regions.json · map/)"
    )
    ap.add_argument("--deck", type=Path, required=True, help="ELMFIRE 덱 폴더(inputs/ 안 래스터)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sigungu", default="의성군")
    ap.add_argument("--margin-m", type=float, default=2000.0)
    ap.add_argument("--tables", type=Path, help="연료 대응표를 읽을 덱(기본 --deck)")
    ap.add_argument(
        "--label", help="meta.json region 글(기본: 「<시군구>(<덱 폴더> 덱, 경계 + margin)」)"
    )
    a = ap.parse_args()

    bounds = sigungu_bounds(a.input, a.sigungu, a.margin_m)
    label = a.label or f"{a.sigungu}({a.deck.name} 덱, 경계 + {a.margin_m / 1000:g} km)"
    t = time.time()
    out = build_grid(a.input, a.deck, a.out, bounds=bounds, tables=a.tables, region=label)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    g = meta["grid"]
    size = sum(p.stat().st_size for p in out.iterdir()) / 1e6
    print(
        f"{label}: {g['w']} × {g['h']}칸({g['cs']:g} m) · {size:.1f} MB · {time.time() - t:.0f}초 → {out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
