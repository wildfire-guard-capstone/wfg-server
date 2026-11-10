"""예측 제공자 — 갈아 끼우는 자리.

새 예측 방법은 이 폴더에 파일 하나를 만들고(base.Predictor 약속을 지킴)
아래 PREDICTORS에 이름을 더한다.
지금: synthetic(가짜 타원) · file(P1 ~ P8 GeoJSON) ·
elmfire(요청 때 만든 기상 입력으로 ELMFIRE 실행 — 출처 stations · deck).
"""

from pathlib import Path
from typing import Any

from app.predict.base import PredictionRequest, PredictionResult, Predictor
from app.predict.file import FilePredictor
from app.predict.synthetic import SyntheticPredictor

PREDICTORS = {
    "synthetic": SyntheticPredictor,
    "file": FilePredictor,
    "elmfire": None,  # 쓸 때 불러온다(spread · ELMFIRE는 GPU 서버에만, API 이미지에는 없음)
}


def get_predictor(name: str, **options: Any) -> Predictor:
    """이름으로 예측 제공자를 만든다. options는 그 예측의 설정(file이면 path 등)."""
    if name not in PREDICTORS:
        raise ValueError(f"모르는 예측: {name} (가능: {', '.join(PREDICTORS)})")
    if name == "file":
        options["path"] = Path(options["path"])
        if options.get("weather_path"):
            options["weather_path"] = Path(options["weather_path"])
    if name == "elmfire":
        from app.predict.elmfire import ElmfirePredictor

        options["deck"] = Path(options["deck"])
        if options.get("work_dir"):
            options["work_dir"] = Path(options["work_dir"])
        return ElmfirePredictor(**options)
    return PREDICTORS[name](**options)


__all__ = ["PREDICTORS", "PredictionRequest", "PredictionResult", "Predictor", "get_predictor"]
