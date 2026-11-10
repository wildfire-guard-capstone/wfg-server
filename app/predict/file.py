"""파일 예측: P1 ~ P8을 담은 GeoJSON 파일을 그대로 예측 결과로 쓴다.

다른 곳에서 계산한 범위(클라우드에서 돌린 ELMFIRE, 다른 모델, 손으로 그린 범위 …)를
코드 수정 없이 넣어 제안을 시험할 때 쓴다.

파일 모양: FeatureCollection, 피처마다 properties.hour = 1 ~ 8, geometry = Polygon · MultiPolygon.
좌표계는 GeoJSON 기본인 WGS84(EPSG:4326). 5179로 저장된 파일이면 crs=5179.
기상: weather 파일(predictions.weather 모양 JSON)이 있으면 그것(범위를 만들 때 쓴 기상),
없으면 서버가 받은 기상, 그것도 없으면 고정값.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform

from app.predict.base import HOURS, PredictionRequest, PredictionResult, fixed_weather


@dataclass(frozen=True)
class FilePredictor:
    path: Path
    crs: int = 4326
    weather_path: Path | None = None
    wind_ms: float = 4.0
    wind_deg: float = 270.0
    name: str = "file"

    def predict(self, request: PredictionRequest) -> PredictionResult:
        data = json.loads(Path(self.path).read_text(encoding="utf-8"))
        by_hour = {}
        for f in data["features"]:
            hour = int(f["properties"]["hour"])
            by_hour[hour] = shape(f["geometry"])
        missing = [h for h in range(1, HOURS + 1) if h not in by_hour]
        if missing:
            raise ValueError(f"{self.path}: P{missing} 범위가 없습니다(hour 1 ~ {HOURS} 필요)")
        slices = [by_hour[h] for h in range(1, HOURS + 1)]
        if self.crs != 5179:
            to_5179 = Transformer.from_crs(self.crs, 5179, always_xy=True).transform
            slices = [transform(to_5179, g) for g in slices]
        # ELMFIRE 결과(app.predict.run_elmfire)를 이 파일로 넣을 때는 그때 쓴 기상 JSON을
        #   weather_path로 같이 준다(docs/ELMFIRE-기상.md)
        if self.weather_path:  # 그 범위를 만들 때 쓴 기상(받은 기상보다 우선 — 실제로 쓴 값)
            weather = json.loads(Path(self.weather_path).read_text(encoding="utf-8"))
        else:
            weather = request.weather or fixed_weather(
                request.base_at, self.wind_ms, self.wind_deg, "FILE_FIXED"
            )
        meta = {"path": str(self.path), "crs": self.crs}
        return PredictionResult(slices, weather, self.name, meta)
