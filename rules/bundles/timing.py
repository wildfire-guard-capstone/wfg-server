"""`time` 묶음: 0시 · 생성 시각 · 요일 · P0 ~ P8 끝 시각 · 해(일몰 · 일출 · 지금 밤인가)."""

from datetime import datetime, timedelta
from math import ceil
from typing import Any

from rules.bundles.common import MAX_P
from rules.context import Context
from rules.core.convert import clock, p_until, weekday, zone_of


def _event(ctx: Context, t: datetime) -> dict[str, Any]:
    """해가 지거나 뜨는 시각과 그 시각이 드는 P · 구간. P8 뒤면 P · 구간은 null."""
    p = ceil((t - ctx.base_at) / timedelta(hours=1))
    p = max(p, 1)
    inside = p <= MAX_P
    return {
        "at": clock(t, ctx.base_at),
        "p": p if inside else None,
        "zone": zone_of(p).value if inside else None,
    }


def time_bundle(ctx: Context) -> dict[str, Any]:
    """`time`: 0시 · 생성 시각 · 요일 · P0 ~ P8 끝 시각(P0 = 「지금」) · 해.

    해 = 0시 뒤 처음 오는 일몰 · 일출(C-05)과 그 시각이 드는 P, 0시가 밤인가(J2).
    """
    return {
        "base_at": ctx.base_at.strftime("%Y-%m-%d %H:%M"),
        "generated_at": ctx.generated_at.strftime("%Y-%m-%d %H:%M"),
        "weekday": weekday(ctx.base_at),
        "until": [p_until(ctx.base_at, p) for p in range(MAX_P + 1)],
        "sun": {
            "sunset": _event(ctx, ctx.sunset),
            "sunrise": _event(ctx, ctx.sunrise),
            "now_is_night": ctx.now_is_night,
        },
    }
