"""`weather` 묶음: 현재 기상 · 0 ~ 8시간 시간별 풍향 · 풍속 · 최대 풍속(R-16).

「강풍」 · 「잦아듦」 · 「풍향 급변」은 수치 기준이 없어 판정하지 않는다 — 시간별 값을 넘기고
LLM이 p.78 문구로 조건문만 쓴다. 바람 시간대 묶기(풍속 차 몇 m/s)도 D 값이라 하지 않는다(11/7 점검).
"""

from datetime import timedelta
from typing import Any

from rules.context import Context
from rules.core.convert import clock, round1
from rules.core.direction import compass16

HOURS = range(0, 9)  # 0시 ~ 8시간 뒤
UNKNOWN_FIXED = [
    {"field": "최대순간풍속", "reason": "단기예보 · 시연 AWS 파일에 없음"},
    {"field": "헬기 운항 가능 여부", "reason": "운항 기준 없음"},
    {"field": "시정 · 기상특보", "reason": "받지 않음"},
    {"field": "풍향 급변", "reason": "기준 없음"},
]


def weather_bundle(ctx: Context) -> dict[str, Any]:
    """`weather`: 시간별 값과 최대 풍속.

    1. 0 ~ 8시간 풍향 · 풍속이 하나라도 없으면 오류(빠진 값을 메우지 않음)
    2. 시간별 = 시각 · 16방위 · 각도 · 풍속
    3. 최대 풍속 = 9개 중 가장 큰 값, 그 값인 시각 모두(「14:50 ~ 16:50」용), 첫 시각의 풍향
    4. 현재 = 0시 값 + 기온 · 습도(받은 그대로)
    """
    inp = ctx.inputs
    missing = [h for h in HOURS if inp.wind_ms.get(h) is None or inp.wind_deg.get(h) is None]
    if missing:  # 1
        raise ValueError(f"풍향 · 풍속 값이 없는 시간: {missing}")
    hourly = [
        {
            "h": h,
            "at": clock(ctx.base_at + timedelta(hours=h), ctx.base_at),
            "dir": compass16(inp.wind_deg[h]),
            "deg": round(inp.wind_deg[h]),
            "ms": round1(inp.wind_ms[h]),
        }
        for h in HOURS
    ]  # 2
    top = max(r["ms"] for r in hourly)  # 3
    at_top = [r for r in hourly if r["ms"] == top]
    now = hourly[0]
    return {
        "now": {  # 4
            "dir": now["dir"],
            "deg": now["deg"],
            "ms": now["ms"],
            "temp_c": inp.temp_c,
            "rh_pct": inp.rh_pct,
        },
        "mean_0_5_ms": round1(ctx.wind.mean_0_5_ms),
        "hourly": hourly,
        "max": {"ms": top, "dir": at_top[0]["dir"], "hours": [r["at"] for r in at_top]},
        "basis": ["SM-p073"],
        "unknown": [dict(u) for u in UNKNOWN_FIXED],
    }
