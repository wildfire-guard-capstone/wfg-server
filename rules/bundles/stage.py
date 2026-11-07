"""`stage` 묶음: 현재 · P5 대응단계(J3 · J4), 추세, 단계 변화."""

from typing import Any

from rules.context import Context
from rules.core.convert import round1
from rules.core.facility import FacilityCount
from rules.core.stage import LABEL, StageJudgment

BASIS_STAGE = ["SM-p013", "SM-p073"]  # 단계 정의 · 단계 판단 기준 표
UNKNOWN_STAGE = [{"field": "진화예상시간", "reason": "MVP에서 제외"}]


def _point(
    stage: StageJudgment, area_ha: float, wind_ms: float, facility: FacilityCount
) -> dict[str, Any]:
    """한 시점(현재 · P5)의 단계 판정과 그 판정에 쓴 값."""
    return {
        "stage": LABEL[stage.stage],
        "area_ha": round1(area_ha),
        "wind_ms": round1(wind_ms),
        "facility": facility.total,
        "facility_is_minimum": facility.is_minimum,
        "by_area": LABEL[stage.by_area],
        "by_wind": None if stage.by_wind is None else LABEL[stage.by_wind],
        "by_facility": LABEL[stage.by_facility],
        "decided_by": list(stage.decided_by),  # 요소 키: area · wind · facility
    }


def stage_bundle(ctx: Context) -> dict[str, Any]:
    """`stage`: 현재 · P5 대응단계, 추세(P5 vs 현재), 단계 변화(이번 vs 직전 제안서)."""
    w, fac = ctx.wind, ctx.facility
    return {
        "now": _point(ctx.stage_now, ctx.areas_ha[0], w.now_ms, fac),
        "p5": _point(ctx.stage_p5, ctx.areas_ha[5], w.mean_0_5_ms, fac),
        "trend": ctx.trend.value,
        "change": {
            "prev": None if ctx.prev_stage is None else LABEL[ctx.prev_stage],
            "now": LABEL[ctx.stage_now.stage],
            "type": ctx.change.value,
        },
        "basis": list(BASIS_STAGE),
        "unknown": [dict(u) for u in UNKNOWN_STAGE],
    }
