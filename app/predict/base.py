"""예측 제공자의 약속. 어떤 예측이든 같은 입력을 받아 같은 모양의 결과를 돌려준다.

예측 방법(가짜 · 파일 · ELMFIRE · 재현 …)은 이 약속만 지키면 갈아 끼울 수 있다.
예측 제공자는 DB를 모른다 — 저장은 repositories, 잇기는 services가 한다.
좌표는 모두 EPSG:5179(m).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from shapely.geometry.base import BaseGeometry

HOURS = 8  # P1 ~ P8


@dataclass(frozen=True)
class PredictionRequest:
    start: BaseGeometry  # P0 = 시작 형상: 현재 화선(없으면 발화점)
    base_at: datetime  # 0시 = 시작 형상의 관측 시각
    site: tuple[float, float]  # 기준점 위도 · 경도(WGS84) — 기상 지점 · 일출 · 일몰
    # TODO(기상 연결, 4-2): weather 칸 — 서버가 app.weather에서 받은 기상. 그러면 예측
    # 제공자는 기상을 만들지 않고 받은 것을 쓴다(PredictionResult.weather 대신).
    # services.prediction.run_prediction 주석 참고


@dataclass(frozen=True)
class PredictionResult:
    slices: list[BaseGeometry]  # P1 ~ P8 누적 확산 범위(면)
    # predictions.weather 모양: {"source", "issued_at", "location", "hours": [9개]}
    weather: dict[str, Any]
    source: str  # 어떤 예측이 만들었나(synthetic · file · elmfire …)
    meta: dict[str, Any] = field(default_factory=dict)  # 예측마다 남길 값(파일 경로, 매개변수 …)


class Predictor(Protocol):
    name: str

    def predict(self, request: PredictionRequest) -> PredictionResult: ...


def fixed_weather(
    base_at: datetime, wind_ms: float, wind_deg: float, source: str, **extra: float
) -> dict[str, Any]:
    """시간마다 같은 값인 기상. base_at을 내림한 정시부터 8시간 뒤까지 9개(DB 설계 2.3)."""
    start = base_at.replace(minute=0, second=0, microsecond=0)
    hours = [
        {
            "at": (start + timedelta(hours=h)).isoformat(),
            "wind_dir": wind_deg,
            "wind_speed": wind_ms,
            **extra,
        }
        for h in range(HOURS + 1)
    ]
    return {"source": source, "issued_at": None, "location": None, "hours": hours}
