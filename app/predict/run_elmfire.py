"""명령: 화선 · 기상 출처로 ELMFIRE를 돌려 P1 ~ P8 GeoJSON을 쓴다(DB · API 없이).

GPU 서버 · worker에서 예측만 확인하거나, 결과를 파일 예측(PREDICTOR=file)으로 넘길 때 쓴다.

  # 시연(덱에 든 의성 관측 기상). --weather = 기온 · 습도 · 강수용 서버 기상 JSON
  # (없으면 고정 시험값)
  python -m app.predict.run_elmfire --deck ~/wfg/demo/deck --start p0.geojson \\
      --base-at 2025-03-22T12:50+09:00 --source deck --weather asos.json --out p1_p8.geojson
  # 실시간(관측소 매분자료 + 단기예보, .env의 KMA_API_KEY · DATA_GO_KR_API_KEY)
  python -m app.predict.run_elmfire --deck ~/wfg/demo/deck --start p0.geojson \\
      --base-at 2026-11-11T14:20+09:00 --source stations --out p1_p8.geojson

--start = 화선(면) 또는 발화점(점) GeoJSON(기본 EPSG:4326). --site = 발화점 위도,경도(저장 · 판정용
9시간을 꺼낼 칸). 출력 = FeatureCollection(EPSG:4326, properties hour · at · area_ha).
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
from app.weather.base import KST, WeatherRequest
from app.weather.elmfire_input import (
    SOURCES,
    ElmfireWeatherRequest,
    ElmfireWeatherSource,
    check_input,
    deck_weather_profile,
)
from app.weather.fixed import FixedWeather


def _read_start(path: Path, crs: int) -> BaseGeometry:
    from pyproj import Transformer
    from shapely.ops import transform, unary_union

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = data["features"] if data.get("type") == "FeatureCollection" else [data]
    geom = unary_union([shape(f.get("geometry", f)) for f in feats])
    if crs != DECK_EPSG:
        geom = transform(Transformer.from_crs(crs, DECK_EPSG, always_xy=True).transform, geom)
    return geom


def _source(name: str, deck: Path) -> ElmfireWeatherSource:
    if name == "deck":
        from app.weather.deck import DeckWeather

        return DeckWeather(deck)
    from app.core.config import get_settings
    from app.weather.field import station_field

    s = get_settings()
    if not (s.kma_api_key and s.data_go_kr_api_key):
        raise SystemExit("stations에는 .env의 KMA_API_KEY · DATA_GO_KR_API_KEY가 필요합니다")
    return station_field(s.kma_api_key, s.data_go_kr_api_key)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="ELMFIRE로 P1 ~ P8을 계산해 GeoJSON으로 쓴다(DB 없음)")
    p.add_argument("--deck", type=Path, required=True)
    p.add_argument("--elmfire", default="elmfire_1.1")
    p.add_argument("--start", type=Path, required=True, help="화선 GeoJSON(면) 또는 발화점(점)")
    p.add_argument("--start-crs", type=int, default=4326)
    p.add_argument(
        "--base-at", required=True, help="화선 관측 시각(ISO, 예 2025-03-22T12:50+09:00)"
    )
    p.add_argument("--source", choices=SOURCES, default="deck", help="기상 출처(시연 · 실시간)")
    p.add_argument("--weather", type=Path, help="deck: 기온 · 습도 · 강수용 서버 기상 JSON")
    p.add_argument("--site", default="36.3657,128.6023", help="발화점 위도,경도")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--work-dir", type=Path)
    p.add_argument("--keep-run", action="store_true")
    a = p.parse_args(argv)

    base_at = datetime.fromisoformat(a.base_at)
    base_at = base_at if base_at.tzinfo else base_at.replace(tzinfo=KST)
    lat, lon = (float(v) for v in a.site.split(","))
    source = _source(a.source, a.deck)
    weather = None
    if source.uses_server_weather:
        weather = (
            json.loads(a.weather.read_text(encoding="utf-8"))
            if a.weather
            else FixedWeather().fetch(WeatherRequest((lat, lon), base_at))
        )
    profile = deck_weather_profile(a.deck)
    request = ElmfireWeatherRequest(base_at, (lat, lon), profile, weather)
    inp = check_input(source.build(request), profile)
    predictor = ElmfirePredictor(a.deck, a.elmfire, a.work_dir, a.keep_run)
    req = PredictionRequest(_read_start(a.start, a.start_crs), base_at, (lat, lon), None, inp)
    result = predictor.predict(req)
    fc = slices_geojson(result.slices, base_at)
    a.out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    areas = " · ".join(
        f"P{f['properties']['hour']} {f['properties']['area_ha']:.0f}" for f in fc["features"]
    )
    print(f"{a.out}: {areas} ha ({result.meta['wall_s']}초, 기상 {a.source})")


if __name__ == "__main__":
    main()
