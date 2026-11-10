"""명령: 화선 · 기상 파일로 ELMFIRE를 돌려 P1 ~ P8 GeoJSON을 쓴다(DB · API 없이).

GPU 서버에서 예측만 확인하거나, 결과를 파일 예측(PREDICTOR=file)으로 넘길 때 쓴다.

  python -m app.predict.run_elmfire --deck ~/wfg/demo/deck --start p0.geojson \\
      --base-at 2025-03-22T12:50+09:00 --weather weather.json --out p1_p8.geojson

--start = 화선(면) 또는 발화점(점) GeoJSON(기본 EPSG:4326). --weather = 9시간 기상 JSON
(predictions.weather 모양: {"hours": [{at, wind_dir, wind_speed, temp, humidity, precip} × 9]}),
없으면 고정 시험값. 출력 = FeatureCollection(EPSG:4326, properties hour · at · area_ha).
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.predict.base import PredictionRequest
from app.predict.elmfire import DECK_EPSG, ElmfirePredictor
from app.predict.geojson import slices_geojson
from app.weather.base import KST


def _read_start(path: Path, crs: int) -> BaseGeometry:
    from pyproj import Transformer
    from shapely.ops import transform, unary_union

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = data["features"] if data.get("type") == "FeatureCollection" else [data]
    geom = unary_union([shape(f.get("geometry", f)) for f in feats])
    if crs != DECK_EPSG:
        geom = transform(Transformer.from_crs(crs, DECK_EPSG, always_xy=True).transform, geom)
    return geom


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="ELMFIRE로 P1 ~ P8을 계산해 GeoJSON으로 쓴다(DB 없음)")
    p.add_argument("--deck", type=Path, required=True)
    p.add_argument("--elmfire", default="elmfire_1.1")
    p.add_argument("--start", type=Path, required=True, help="화선 GeoJSON(면) 또는 발화점(점)")
    p.add_argument("--start-crs", type=int, default=4326)
    p.add_argument(
        "--base-at", required=True, help="화선 관측 시각(ISO, 예 2025-03-22T12:50+09:00)"
    )
    p.add_argument("--weather", type=Path, help="9시간 기상 JSON(predictions.weather 모양)")
    p.add_argument("--site", default="36.36,128.60", help="기준점 위도,경도(기상이 없을 때만)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--work-dir", type=Path)
    p.add_argument("--keep-run", action="store_true")
    p.add_argument("--wind-mult", type=float, default=1.0)
    a = p.parse_args(argv)

    base_at = datetime.fromisoformat(a.base_at)
    base_at = base_at if base_at.tzinfo else base_at.replace(tzinfo=KST)
    weather = json.loads(a.weather.read_text(encoding="utf-8")) if a.weather else None
    lat, lon = (float(v) for v in a.site.split(","))
    predictor = ElmfirePredictor(a.deck, a.elmfire, a.work_dir, a.keep_run, a.wind_mult)
    req = PredictionRequest(_read_start(a.start, a.start_crs), base_at, (lat, lon), weather)
    result = predictor.predict(req)
    fc = slices_geojson(result.slices, base_at)
    a.out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    areas = " · ".join(
        f"P{f['properties']['hour']} {f['properties']['area_ha']:.0f}" for f in fc["features"]
    )
    print(f"{a.out}: {areas} ha ({result.meta['wall_s']}초)")


if __name__ == "__main__":
    main()
