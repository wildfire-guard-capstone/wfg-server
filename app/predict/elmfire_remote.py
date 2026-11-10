"""ELMFIRE를 worker 컨테이너에서 돌리는 예측(구조 B) — PREDICTOR=elmfire + ELMFIRE_WORKER_URL.

API는 예측 요청 때 기상 입력을 만들고(못 만들면 503), 작업에서 이 예측이 화선 · 기상 입력을
worker(/run)로 보낸다. worker가 ELMFIRE를 돌려 P1 ~ P8을 돌려준다(app.predict.worker_server).
결과 기상 = 입력의 발화점 칸 9시간(API가 이미 갖고 있다).
spread · rasterio · ELMFIRE를 부르지 않는다 — API 이미지에서 돈다.

오류: worker 422(입력 · 화선 문제) → ValueError(작업 실패 PREDICTION_INVALID),
     시간 초과 · ELMFIRE 실패 · 연결 실패 → RuntimeError(작업 실패 INTERNAL_ERROR).
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from app.predict.base import PredictionRequest, PredictionResult
from app.predict.elmfire_wire import pack_request, unpack_result


def _message(e: urllib.error.HTTPError) -> str:
    try:
        return json.loads(e.read().decode("utf-8"))["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return str(e)


@dataclass(frozen=True)
class ElmfireWorker:
    url: str  # 예: http://wfg-worker:8100 (같은 Docker 네트워크)
    timeout_s: float = 900.0  # 앞 실행을 기다리는 시간까지(ELMFIRE 1회 약 20초 · 상한 5분)
    name: str = "elmfire"

    def predict(self, request: PredictionRequest) -> PredictionResult:
        inp = request.elmfire_weather
        if inp is None:
            raise ValueError(
                "ELMFIRE 기상 입력이 없습니다 — 예측 요청 때 출처(stations · deck)로 만들어"
                " 넘깁니다"
            )
        req = urllib.request.Request(
            f"{self.url.rstrip('/')}/run",
            data=pack_request(request),
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                slices, meta = unpack_result(r.read())
        except urllib.error.HTTPError as e:
            message = _message(e)
            if e.code == 422:
                raise ValueError(message) from None
            raise RuntimeError(f"ELMFIRE worker {e.code}: {message}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"ELMFIRE worker에 연결하지 못했습니다({self.url}): {e}") from None
        return PredictionResult(slices, inp.weather, self.name, {**meta, "worker": self.url})


__all__ = ["ElmfireWorker"]
