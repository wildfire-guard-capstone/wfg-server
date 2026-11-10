"""API ↔ ELMFIRE worker 사이에 오가는 모양(구조 B, docs/ELMFIRE-기상.md).

요청 = 화선 · 화선 시각 · 발화점 + ELMFIRE 기상 입력(app.weather.elmfire_input) 한 덩어리(npz).
  header(JSON 글자) + grid_ws … grid_m100(9밴드) + lead_ws … lead_m100(있으면 1밴드)
응답 = P1 ~ P8(GeoJSON 도형, EPSG:5179) + meta(JSON).

numpy · shapely만 쓴다 — API 이미지와 worker 이미지 둘 다에서 불러온다.
"""

import io
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import numpy as np
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from app.predict.base import PredictionRequest
from app.weather.elmfire_input import GRID_KEYS, ElmfireWeatherInput


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def pack_request(request: PredictionRequest) -> bytes:
    """예측 요청(기상 입력 포함) → npz 바이트. 기상 입력이 없으면 ValueError."""
    inp = request.elmfire_weather
    if inp is None:
        raise ValueError("ELMFIRE 기상 입력이 없습니다")
    header = {
        "start": mapping(request.start),
        "base_at": request.base_at.isoformat(),
        "site": list(request.site),
        "t0": inp.t0.isoformat(),
        "weather": inp.weather,
        "meta": inp.meta,
        "lead": inp.lead is not None,
    }
    arrays = {f"grid_{k}": np.asarray(inp.grids[k], "float32") for k in GRID_KEYS}
    if inp.lead is not None:
        arrays |= {f"lead_{k}": np.asarray(inp.lead[k], "float32") for k in GRID_KEYS}
    buf = io.BytesIO()
    np.savez_compressed(buf, header=np.frombuffer(_json(header), dtype=np.uint8), **arrays)
    return buf.getvalue()


def unpack_request(body: bytes) -> PredictionRequest:
    """npz 바이트 → 예측 요청(기상 입력 포함). 모양이 틀리면 ValueError."""
    try:
        z = np.load(io.BytesIO(body), allow_pickle=False)
        header = json.loads(z["header"].tobytes().decode("utf-8"))
        grids = {k: z[f"grid_{k}"] for k in GRID_KEYS}
        lead = {k: z[f"lead_{k}"] for k in GRID_KEYS} if header["lead"] else None
    except (OSError, KeyError, ValueError) as e:
        raise ValueError(f"ELMFIRE 요청 모양이 틀립니다: {e}") from None
    inp = ElmfireWeatherInput(
        datetime.fromisoformat(header["t0"]), grids, header["weather"], header["meta"], lead
    )
    return PredictionRequest(
        shape(header["start"]),
        datetime.fromisoformat(header["base_at"]),
        tuple(header["site"]),
        None,
        inp,
    )


def pack_result(slices: Sequence[BaseGeometry], meta: dict[str, Any]) -> bytes:
    return _json({"slices": [mapping(g) for g in slices], "meta": meta})


def unpack_result(body: bytes) -> tuple[list[BaseGeometry], dict[str, Any]]:
    data = json.loads(body.decode("utf-8"))
    return [shape(g) for g in data["slices"]], data.get("meta") or {}


def pack_error(code: str, message: str) -> bytes:
    return _json({"error": {"code": code, "message": message}})


__all__ = ["pack_error", "pack_request", "pack_result", "unpack_request", "unpack_result"]
