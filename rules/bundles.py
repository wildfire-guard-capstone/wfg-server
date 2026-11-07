"""엔진 JSON 묶음: 공통 결과(ctx)를 주제별 고정 모양 JSON으로 바꾼다(11/7).

엔진은 항목을 모른다. 묶음은 사실과 기준 판정만 담고, 할 일의 이름(협의 · 인계 등)은
붙이지 않는다 — 그것은 LLM이 매뉴얼로 정한다. 항목 입력(items/)은 이 묶음에서
고르기 · 옮겨 담기 · 이름 바꾸기 · 틀 채우기만 한다.

고정 모양: 키는 늘 있다. 없으면 `[]` · `0` · `false` · `null`. 묶음마다 `basis`(판정 근거
청크 ID) · `unknown`(모르는 칸과 이유)을 둔다. 지금은 S1이 쓰는 묶음만 있다.
"""

from typing import Any

from rules.context import Context
from rules.core.area import first_over
from rules.core.commander import LARGE_FIRE_HA, NATIONAL_HA, Commander, Level, Reason, ReasonKind
from rules.core.convert import num_text, p_until, round1, weekday
from rules.core.facility import FacilityCount
from rules.core.stage import LABEL, StageJudgment, next_area_threshold

MAX_P = 8

# 판정 근거 청크
BASIS_STAGE = ["SM-p013", "SM-p073"]  # 단계 정의 · 단계 판단 기준 표
BASIS_COMMANDER = ["SM-p072"]  # 단계별 통합지휘권자 · 단서 ①②
BASIS_AREA = ["SM-p114", "DEC-FDPA-a21-h01-02"]  # 대형산불 · 시행령 제21조
BASIS_SPAN = ["LAW-FDPA-a34-h03-04"]  # 2개 이상 시 · 군 · 구 / 시 · 도

LEVEL_CODE = {Level.LOCAL: "sigungu", Level.SIDO: "sido", Level.NATIONAL: "national"}
UNKNOWN_STAGE = [{"field": "진화예상시간", "reason": "MVP에서 제외"}]


def time_bundle(ctx: Context) -> dict[str, Any]:
    """`time`: 0시 · 생성 시각 · 요일 · P0 ~ P8 끝 시각(P0 = 「지금」)."""
    return {
        "base_at": ctx.base_at.strftime("%Y-%m-%d %H:%M"),
        "generated_at": ctx.generated_at.strftime("%Y-%m-%d %H:%M"),
        "weekday": weekday(ctx.base_at),
        "until": [p_until(ctx.base_at, p) for p in range(MAX_P + 1)],
    }


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


def commander_bundle(ctx: Context) -> dict[str, Any]:
    """`commander`: 읽는 사람 · 지금(현재 화선) · 곧(P5) 지휘권자와 이유.

    인계할지 · 누가 인수할지는 LLM이 세 이름을 보고 정한다(11/7, `handover` 뺌).
    근거 청크 = p.72 + 이유에 면적이 있으면 대형산불 근거 + 걸친 지역이 있으면 법 제34조.
    """
    reasons = ctx.commander_now.reasons + ctx.commander_next.reasons
    basis = list(BASIS_COMMANDER)
    if any(r.kind is ReasonKind.AREA for r in reasons):
        basis += BASIS_AREA
    if any(r.kind is not ReasonKind.AREA for r in reasons):
        basis += BASIS_SPAN
    return {
        "reader": ctx.reader,
        "now": _commander(ctx.commander_now),
        "next": _commander(ctx.commander_next),
        "basis": basis,
        "unknown": [],
    }


def build_bundles(ctx: Context) -> dict[str, Any]:
    """공통 결과를 엔진 JSON(주제별 묶음)으로 바꾼다. `proposals.context`에 그대로 저장한다."""
    return {
        "time": time_bundle(ctx),
        "area": area_bundle(ctx),
        "stage": stage_bundle(ctx),
        "commander": commander_bundle(ctx),
    }
