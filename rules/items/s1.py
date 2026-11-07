"""S1 대응단계 판단 · 격상 검토 · 지휘권 인계: 공통 결과(ctx)로 S1 엔진 결과를 만든다.

결과는 `proposal_items.detail`에 그대로 저장되고 LLM 입력의 항목 부분이 된다.
공통 규칙 함수(면적 · 단계 · 지휘권자)는 다시 부르지 않고 ctx에서 꺼낸다.
S1만 쓰는 작은 판정(상황 · 단계 변화 · 인계 · 기준 면적 넘는 구간)은 여기서 한다(11/7).
구분(즉시 · 대기 · 협의)은 LLM이 출력하므로 넣지 않는다(11/7).
"""

from typing import Any

from rules.context import Context
from rules.core.area import first_over
from rules.core.commander import (
    LARGE_FIRE_HA,
    NATIONAL_HA,
    Commander,
    Handover,
    Reason,
    ReasonKind,
    decide_handover,
)
from rules.core.convert import num_text, p_clock, round1
from rules.core.facility import FacilityCount
from rules.core.stage import LABEL, Stage, StageJudgment, next_area_threshold, stage_change

ID, NAME = "S1", "대응단계 판단·격상 검토·지휘권 인계"

ESCALATION_EXPECTED, MAINTAIN = "escalation_expected", "maintain"

# 판정 근거 청크(문단 근거인 p.91 등은 블록이 붙인다)
BASIS_STAGE = ["SM-p073"]  # 단계 판단 기준 표
BASIS_COMMANDER = ["SM-p072"]  # 단계별 통합지휘권자 · 단서 ①②
BASIS_AREA = ["SM-p114", "DEC-FDPA-a21-h01-02"]  # 대형산불 · 시행령 제21조
BASIS_SPAN = ["LAW-FDPA-a34-h03-04"]  # 2개 이상 시 · 군 · 구 / 시 · 도

WITHHELD = [{"field": "진화예상시간", "reason": "MVP에서 제외"}]


def _factor_texts(
    stage: StageJudgment, area_ha: float, wind_ms: float, facility: FacilityCount
) -> list[str]:
    """단계를 정한 요소를 글로 쓴다: 「피해면적 48.5 ha」 · 「시설피해 우려 최소 2동」."""
    minimum = "최소 " if facility.is_minimum else ""
    texts = {
        "area": f"피해면적 {num_text(area_ha)} ha",
        "wind": f"평균풍속 {num_text(wind_ms)} m/s",
        "facility": f"시설피해 우려 {minimum}{facility.total}동",
    }
    return [texts[name] for name in stage.decided_by]


def _point(
    stage: StageJudgment, area_ha: float, wind_ms: float, facility: FacilityCount
) -> dict[str, Any]:
    """한 시점(현재 · P5)의 판정을 facts 모양으로 담는다."""
    return {
        "stage": LABEL[stage.stage],
        "area_ha": round1(area_ha),
        "wind_ms": round1(wind_ms),
        "facility": facility.total,
        "facility_is_minimum": facility.is_minimum,
        "by_area": LABEL[stage.by_area],
        "by_wind": None if stage.by_wind is None else LABEL[stage.by_wind],
        "by_facility": LABEL[stage.by_facility],
        "decided_by": _factor_texts(stage, area_ha, wind_ms, facility),
    }


def _reason_text(reason: Reason) -> str:
    if reason.kind is ReasonKind.AREA:
        suffix = " 대형산불" if reason.threshold_ha == LARGE_FIRE_HA else ""
        return f"피해면적 {num_text(reason.threshold_ha)} ha 이상{suffix}"
    unit = "시·도" if reason.kind is ReasonKind.SPAN_SIDO else "시·군·구"
    return f"2개 이상 {unit}에 걸침({' · '.join(reason.names)})"


def _handover_reasons(handover: Handover, now: Commander, next_: Commander) -> tuple[Reason, ...]:
    """인계 상황에 맞는 이유: 대비 → 곧 지휘권자를 만든 조건, 그 밖 → 지금 지휘권자의 조건."""
    return next_.reasons if handover is Handover.PREPARE else now.reasons


def judge_s1(ctx: Context, prev_stage: Stage | None, reader: str) -> dict[str, Any]:
    """S1 엔진 결과를 만든다. `prev_stage` = 직전 제안서의 현재 단계, `reader` = 읽는 사람 직함.

    1. ctx에서 현재 · P5 대응단계를 꺼낸다
    2. 상황: P5가 현재보다 높으면 escalation_expected, 아니면 maintain
    3. 단계 변화: 직전 단계와 현재 단계를 비교(new · up · same · down)
    4. 인계: 읽는 사람 · 지금 · 곧 지휘권자로 검토 · 대비 · 없음, 상황에 맞는 이유를 고른다
    5. 기준 면적 넘는 구간: 다음 단계 면적 기준 · 100 ha · 1,000 ha 중 처음 넘는 구간과 시각
    6. 면적 흐름: P1 ~ P8 면적에 구간 끝 시각을 붙인다
    7. 글 만들기: 단계를 정한 요소 · 이유를 LLM이 읽을 말로 쓴다
    8. finding: 상황마다 정한 틀에 값을 채운다
    9. 비운 칸(진화예상시간) · 판정 근거 청크를 붙인다
    10. 하나로 묶어 돌려준다
    """
    # 1
    now, p5, w, fac = ctx.stage_now, ctx.stage_p5, ctx.wind, ctx.facility
    area_now, area_p5 = ctx.areas_ha[0], ctx.areas_ha[5]
    # 2
    status = ESCALATION_EXPECTED if p5.stage > now.stage else MAINTAIN
    # 3
    change = stage_change(prev_stage, now.stage)
    # 4
    c_now, c_next = ctx.commander_now, ctx.commander_next
    handover = decide_handover(reader, c_now.title, c_next.title)
    reasons = _handover_reasons(handover, c_now, c_next)
    # 5
    thresholds = {t for t in (next_area_threshold(now.stage), LARGE_FIRE_HA, NATIONAL_HA) if t}
    slices = [(p, ctx.areas_ha[p]) for p in range(1, 9)]
    crossings = first_over(area_now, slices, thresholds)
    # 6 · 7
    facts = {
        "now": _point(now, area_now, w.now_ms, fac),
        "p5": _point(p5, area_p5, w.mean_0_5_ms, fac),
        "change": {
            "prev": None if prev_stage is None else LABEL[prev_stage],
            "now": LABEL[now.stage],
            "type": change.value,
        },
        "area": {
            "now_ha": round1(area_now),
            "slices": [
                {"p": p, "until": p_clock(ctx.base_at, p), "ha": round1(ha)} for p, ha in slices
            ],
            "first_over": [
                {
                    "ha": round1(c.threshold_ha),
                    "p": c.p,
                    "until": p_clock(ctx.base_at, c.p),
                    "area_ha": round1(c.area_ha),
                }
                for c in crossings
            ],
        },
        "commander": {
            "reader": reader,
            "now": c_now.title,
            "next": c_next.title,
            "handover": handover.value,
            "reason": " · ".join(_reason_text(r) for r in reasons) or None,
        },
    }
    # 8
    finding = _finding(status, prev_stage, facts["now"], facts["p5"], c_now, c_next)
    # 9
    rule_basis = BASIS_STAGE + BASIS_COMMANDER
    if any(r.kind is ReasonKind.AREA for r in reasons):
        rule_basis += BASIS_AREA
    if any(r.kind is not ReasonKind.AREA for r in reasons):
        rule_basis += BASIS_SPAN
    # 10
    return {
        "id": ID,
        "name": NAME,
        "status": status,
        "finding": finding,
        "facts": facts,
        "targets": [],  # 산불 전체에 대한 판정이라 지도 강조 대상 없음
        "withheld": [dict(w) for w in WITHHELD],
        "rule_basis": rule_basis,
    }


def _basis(point: dict[str, Any]) -> str:
    """finding 괄호 안: 단계를 정한 요소, 초기 대응이면 「피해면적 ○ ha, 시설피해 우려 없음」."""
    if not point["decided_by"]:
        return f"피해면적 {num_text(point['area_ha'])} ha, 시설피해 우려 없음"
    return " · ".join(point["decided_by"])


def _finding(
    status: str,
    prev_stage: Stage | None,
    now: dict[str, Any],
    p5: dict[str, Any],
    c_now: Commander,
    c_next: Commander,
) -> str:
    """상황마다 정한 틀에 값을 채운다(규칙엔진 S1 finding 틀).

    1. 괄호 안 근거 = 단계를 정한 요소(초기 대응이면 「피해면적 ○ ha, 시설피해 우려 없음」)
    2. 직전 제안서가 있으면 현재 단계 괄호에 「직전 ○」을 붙인다
    3. escalation_expected: 「현재 기준 ○(…) · P5 기준 ○(…) → 5시간 안에 ○ 기준 도달 예상」
       maintain, 두 단계가 같음: 「현재 · P5 기준 모두 ○(현재 근거 → P5 근거)」
       maintain, P5가 더 낮음: 「현재 기준 ○(…) · P5 기준 ○(…)」(하향 기준이 없어 유지로 봄)
    4. 끝에 「지휘권자 지금」, 바뀌면 「 → 곧」
    """
    basis_now, basis_p5 = _basis(now), _basis(p5)  # 1
    prev = "" if prev_stage is None else f", 직전 {LABEL[prev_stage]}"  # 2
    s_now, s_p5 = now["stage"], p5["stage"]
    # 3
    if status == ESCALATION_EXPECTED:
        head = (
            f"현재 기준 {s_now}({basis_now}{prev}) · P5 기준 {s_p5}({basis_p5})"
            f" → 5시간 안에 {s_p5} 기준 도달 예상"
        )
    elif s_now == s_p5:
        head = f"현재 · P5 기준 모두 {s_now}({basis_now} → {basis_p5}{prev})"
    else:
        head = f"현재 기준 {s_now}({basis_now}{prev}) · P5 기준 {s_p5}({basis_p5})"
    # 4
    commander = c_now.title if c_now.title == c_next.title else f"{c_now.title} → {c_next.title}"
    return f"{head} · 지휘권자 {commander}"
