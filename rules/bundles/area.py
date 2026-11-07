"""`area` 묶음: 현재 · P1 ~ P8 면적과 끝 시각, 기준 면적을 처음 넘는 구간(R-15)."""

from typing import Any

from rules.bundles.common import MAX_P
from rules.context import Context
from rules.core.area import first_over
from rules.core.commander import LARGE_FIRE_HA, NATIONAL_HA
from rules.core.convert import p_until, round1
from rules.core.stage import next_area_threshold


def area_bundle(ctx: Context) -> dict[str, Any]:
    """`area`: 현재 · P1 ~ P8 면적과 끝 시각, 기준 면적을 처음 넘는 구간.

    1. P1 ~ P8 면적에 끝 시각을 붙인다
    2. 기준 = 다음 단계 면적 기준 · 100 ha(대형산불) · 1,000 ha 중 지금 면적보다 큰 것
    3. 기준마다 P8 안에서 처음 넘는 구간 — 넘는 게 없으면 넣지 않는다(11/7)
    """
    area_now = ctx.areas_ha[0]
    slices = [(p, ctx.areas_ha[p]) for p in range(1, MAX_P + 1)]  # 1
    thresholds = {
        t for t in (next_area_threshold(ctx.stage_now.stage), LARGE_FIRE_HA, NATIONAL_HA) if t
    }  # 2
    crossings = first_over(area_now, slices, thresholds)  # 3
    return {
        "now_ha": round1(area_now),
        "slices": [
            {"p": p, "until": p_until(ctx.base_at, p), "ha": round1(ha)} for p, ha in slices
        ],
        "first_over": [
            {
                "ha": round1(c.threshold_ha),
                "p": c.p,
                "until": p_until(ctx.base_at, c.p),
                "area_ha": round1(c.area_ha),
            }
            for c in crossings
        ],
        "unknown": [],
    }
