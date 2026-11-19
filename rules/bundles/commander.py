"""`commander` 묶음: 읽는 사람 · 지금 · 곧 지휘권자와 이유(J6)."""

from typing import Any

from rules.context import Context
from rules.core.commander import LARGE_FIRE_HA, Commander, Level, Reason, ReasonKind
from rules.core.convert import num_text, p_from, p_until, round1

BASIS_COMMANDER = ["SM-p072"]  # 단계별 통합지휘권자 · 단서 ①②
BASIS_AREA = ["SM-p114", "DEC-FDPA-a21-h01-02"]  # 대형산불 · 시행령 제21조
BASIS_SPAN = ["LAW-FDPA-a34-h03-04"]  # 2개 이상 시 · 군 · 구 / 시 · 도
LEVEL_CODE = {Level.LOCAL: "sigungu", Level.SIDO: "sido", Level.NATIONAL: "national"}


def _reason(reason: Reason) -> dict[str, Any]:
    """지휘권자를 정한 조건 하나: 종류 · 면적 기준 · 걸친 지역 · 글."""
    if reason.kind is ReasonKind.AREA:
        suffix = " 대형산불" if reason.threshold_ha == LARGE_FIRE_HA else ""
        text = f"피해면적 {num_text(reason.threshold_ha)} ha 이상{suffix}"
        return {"kind": "area", "ha": round1(reason.threshold_ha), "regions": [], "text": text}
    unit = "시 · 도" if reason.kind is ReasonKind.SPAN_SIDO else "시 · 군 · 구"
    text = f"2개 이상 {unit}에 걸침({' · '.join(reason.names)})"
    return {"kind": reason.kind.value, "ha": None, "regions": list(reason.names), "text": text}


def _commander(c: Commander) -> dict[str, Any]:
    return {
        "title": c.title,
        "level": LEVEL_CODE[c.level],
        "reasons": [_reason(r) for r in c.reasons],
    }


def _step(ctx: Context, p: int, c: Commander) -> dict[str, Any]:
    """지휘권자가 올라가는 구간 하나: 구간 시각(from ~ until)에 그 지휘권자 · 이유를 붙인다."""
    return {
        "p": p,
        "from": p_from(ctx.base_at, p),
        "until": p_until(ctx.base_at, p),
        **_commander(c),
    }


def commander_bundle(ctx: Context) -> dict[str, Any]:
    """`commander`: 읽는 사람 · 지금(현재 화선) · 곧(P5) 지휘권자와 이유 · 올라가는 구간.

    인계할지 · 누가 인수할지는 LLM이 세 이름을 보고 정한다(11/7, `handover` 뺌).
    `steps` = 지휘권자가 올라가는 구간과 시각(11/19 평가 R1). 「곧」만 넘기면 LLM이 「5시간 뒤
    이관」으로 써서, 기준을 처음 넘는 구간의 시각 · 이유를 함께 넘긴다. 지금 = 곧이면 빈 목록.
    근거 청크 = p.72 + 이유에 면적이 있으면 대형산불 근거 + 걸친 지역이 있으면 법 제34조.
    """
    steps = [_step(ctx, p, c) for p, c in ctx.commander_steps]
    reasons = ctx.commander_now.reasons + ctx.commander_next.reasons
    reasons += tuple(r for _, c in ctx.commander_steps for r in c.reasons)
    basis = list(BASIS_COMMANDER)
    if any(r.kind is ReasonKind.AREA for r in reasons):
        basis += BASIS_AREA
    if any(r.kind is not ReasonKind.AREA for r in reasons):
        basis += BASIS_SPAN
    return {
        "reader": ctx.reader,
        "now": _commander(ctx.commander_now),
        "next": _commander(ctx.commander_next),
        "steps": steps,
        "basis": basis,
        "unknown": [],
    }
