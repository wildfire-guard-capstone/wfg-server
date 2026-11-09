"""가짜 예측: 시작 형상에서 시간마다 일정하게 커지는 범위(테스트 · 제안 시험용).

바람 방향으로 늘어난 타원이다. 바람 방향은 받은 기상(없으면 고정값)의 0시 풍향.
P(n) = 시작 형상 ∪ 타원(n) 이라 늘 시작 형상을 덮고, 시간이 갈수록 커진다(P(n) ⊇ P(n−1)).

기본값은 약한 바람의 초기 산불 정도다(11/9): 풍속 2 m/s(바람 기준 초기 대응, 평소 평균 풍속은
대개 3 m/s 아래), 시간당 150 m → 발화점에서 5시간 뒤(P5) 약 71 ha.
"""

from dataclasses import dataclass
from math import cos, radians, sin

from shapely import affinity
from shapely.geometry import Point

from app.predict.base import HOURS, PredictionRequest, PredictionResult, fixed_weather


@dataclass(frozen=True)
class SyntheticPredictor:
    name: str = "synthetic"
    grow_m: float = 150.0  # 한 시간에 바람 방향으로 퍼지는 거리
    side_ratio: float = 0.4  # 옆으로 퍼지는 비율(바람 방향 대비)
    wind_ms: float = 2.0  # 3 m/s 아래 = 바람 기준 초기 대응
    wind_deg: float = 270.0  # 불어오는 방향(서풍 = 270 → 동쪽으로 퍼짐)

    def predict(self, request: PredictionRequest) -> PredictionResult:
        weather = request.weather or fixed_weather(
            request.base_at, self.wind_ms, self.wind_deg, "SYNTHETIC"
        )
        center = request.start.centroid
        to_deg = (weather["hours"][0]["wind_dir"] + 180) % 360  # 퍼지는 방향(북 = 0, 시계 방향)
        dx, dy = sin(radians(to_deg)), cos(radians(to_deg))
        slices = []
        for h in range(1, HOURS + 1):
            reach = self.grow_m * h
            # 원을 옆 방향으로 줄여 타원을 만들고,
            # 바람 쪽으로 반만큼 밀어 시작점 쪽은 덜 퍼지게 한다
            ellipse = affinity.scale(Point(0, 0).buffer(reach, 64), 1.0, self.side_ratio)
            ellipse = affinity.rotate(ellipse, 90 - to_deg, origin=(0, 0))
            ellipse = affinity.translate(
                ellipse, center.x + dx * reach * 0.5, center.y + dy * reach * 0.5
            )
            shape = ellipse.union(request.start.buffer(1.0))
            slices.append(shape.union(slices[-1]) if slices else shape)
        meta = {"grow_m": self.grow_m, "side_ratio": self.side_ratio}
        return PredictionResult(slices, weather, self.name, meta)
