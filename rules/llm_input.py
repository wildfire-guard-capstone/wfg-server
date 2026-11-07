"""LLM 입력 조립: 엔진 입력 → 공통 결과 → 엔진 묶음 → 12개 항목 입력 + 섹션(11/7 형식).

이 결과가 `proposals.context`(묶음) · `proposal_items.detail`(항목)로 저장되고, 에이전트가 항목마다
LLM을 부른다(action 블록이 있는 항목만). 판정은 묶음까지이고, 여기서는 순서대로 모으기만 한다.
"""

from collections.abc import Callable
from typing import Any

from rules.bundles import build_bundles
from rules.context import Inputs, build_context
from rules.items.e1 import e1_input
from rules.items.e2 import e2_input
from rules.items.e3 import e3_input
from rules.items.e4 import e4_input
from rules.items.e5 import e5_input
from rules.items.e6 import e6_input
from rules.items.s1 import s1_input
from rules.items.s2 import s2_input
from rules.items.s3 import s3_input
from rules.items.s4 import s4_input
from rules.items.s5 import s5_input
from rules.items.s6 import s6_input
from rules.sections.e1 import e1_sections
from rules.sections.e2 import e2_sections
from rules.sections.e3 import e3_sections
from rules.sections.e4 import e4_sections
from rules.sections.e5 import e5_sections
from rules.sections.s1 import s1_sections
from rules.sections.s2 import s2_sections
from rules.sections.s3 import s3_sections
from rules.sections.s4 import s4_sections
from rules.sections.s5 import s5_sections
from rules.sections.s6 import e6_sections, s6_sections

Bundles = dict[str, Any]
# 항목 순서(11/7 12개). 섹션 함수가 묶음을 더 보는 항목(S1 · E1)은 묶음도 넘긴다
ITEMS: list[tuple[Callable[[Bundles], Any], Callable[..., Any], bool]] = [
    (s1_input, s1_sections, True),
    (s2_input, s2_sections, False),
    (s3_input, s3_sections, False),
    (s4_input, s4_sections, False),
    (s5_input, s5_sections, False),
    (s6_input, s6_sections, False),
    (e1_input, e1_sections, True),
    (e2_input, e2_sections, False),
    (e3_input, e3_sections, False),
    (e4_input, e4_sections, False),
    (e5_input, e5_sections, False),
    (e6_input, e6_sections, False),
]
STAGE_NAME = {"초기 대응": "초기 대응", "1단계": "확산대응 1단계", "2단계": "확산대응 2단계"}


def build_items(bundles: Bundles) -> list[dict[str, Any]]:
    """항목마다 입력을 만들고 섹션을 붙인다. 만들지 않는 항목(None)은 빼고, 표 행은 섹션 안에만."""
    items = []
    for make, sections, with_bundles in ITEMS:
        item = make(bundles)
        if item is None:
            continue
        sec = sections(item, bundles) if with_bundles else sections(item)
        item.pop("rows", None)
        items.append({**item, **sec})
    return items


def build_common(bundles: Bundles, incident: dict[str, Any]) -> dict[str, Any]:
    """모든 호출의 `common`: 읽는 사람 · 산불(어댑터가 DB에서) · 제안서 시각 · 단계 · 지휘권자."""
    t, c = bundles["time"], bundles["commander"]
    return {
        "reader": c["reader"],
        "incident": incident,
        "proposal": {
            "base_at": t["base_at"],
            "generated_at": t["generated_at"],
            "weekday": t["weekday"],
        },
        "current_stage": STAGE_NAME[bundles["stage"]["now"]["stage"]],
        "commander": {"now": c["now"]["title"], "next": c["next"]["title"]},
    }


def build_llm_input(inputs: Inputs, incident: dict[str, Any]) -> dict[str, Any]:
    """엔진 입력 하나로 엔진 JSON(묶음)과 LLM 입력(공통 + 항목)을 함께 만든다."""
    bundles = build_bundles(build_context(inputs))
    return {
        "bundles": bundles,
        "common": build_common(bundles, incident),
        "items": build_items(bundles),
    }
