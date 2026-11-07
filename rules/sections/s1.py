"""S1 섹션: S1 입력을 보고 LLM 입력의 `sections`(소제목 + 문단 자리)를 만든다.

판정하지 않는다. 블록 주제(`topic`)는 자리마다 고정하고, 블록은 그 사실이 있을 때만 넣는다
(11/7 — 조건에 따라 「발령 협의 / 단계 확인」처럼 주제를 바꾸면 처방을 조립기가 정하는 셈).
협의할지 · 인계할지는 LLM이 사실과 근거 청크를 보고 정한다.
"""

from typing import Any

TITLE_STAGE, TITLE_COMMAND = "1. 대응단계 판단·격상 검토", "2. 지휘권 인계"

# 문단 근거(anchors) → 근거 청크 ID. 근거 검색은 판정 근거(rule_basis)와 이 ID를 합쳐 가져온다
ANCHOR_IDS = {
    "p.72": "SM-p072",
    "p.73": "SM-p073",
    "p.91": "SM-p091",
    "p.114": "SM-p114",
    "시행령 제21조①": "DEC-FDPA-a21-h01-02",
    "시행령 제21조②": "DEC-FDPA-a21-h01-02",
    "법 제34조②": "LAW-FDPA-a34-h03-04",
}
# 지휘권자를 정한 이유 종류 → 붙일 근거
REASON_ANCHORS = {
    "area": ["p.114", "시행령 제21조①", "시행령 제21조②"],
    "span_sigungu": ["법 제34조②"],
    "span_sido": ["법 제34조②"],
}


def _action(n: int, topic: str, use: list[str], anchors: list[str]) -> dict[str, Any]:
    return {"type": "action", "id": f"S1-a{n}", "topic": topic, "use": use, "anchors": anchors}


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def s1_sections(s1: dict[str, Any], bundles: dict[str, Any]) -> dict[str, Any]:
    """S1 입력과 엔진 묶음으로 섹션 · 언급 금지 · 문단 근거 ID를 만든다.

    1. a1 현재 기준 단계 — 늘 넣음
    2. a2 5시간 뒤 단계 — 상황이 escalation_expected일 때만.
       P5 단계를 면적이 정했으면 면적 흐름도 봄
    3. a3 지휘권자 — 늘 넣음. 근거는 p.72 + 지금 · 곧 지휘권자를 정한 이유 종류별 근거
    4. 언급 금지(do_not_state) = 비운 칸(withheld)의 이름
    5. 문단 근거(anchors)를 근거 청크 ID로 바꿔 모은다(anchor_basis)
    """
    facts = s1["facts"]
    blocks = [_action(1, "현재 기준 단계", ["now", "change"], ["p.73", "p.91"])]  # 1
    if s1["status"] == "escalation_expected":  # 2
        use = ["p5", "area"] if "area" in bundles["stage"]["p5"]["decided_by"] else ["p5"]
        blocks.append(_action(2, "5시간 뒤 단계", use, ["p.73", "p.91"]))
    # 3
    commander = facts["commander"]
    kinds = [r["kind"] for side in ("now", "next") for r in commander[side]["reasons"]]
    anchors = _unique(["p.72"] + [a for k in kinds for a in REASON_ANCHORS[k]])
    sections = [
        {"title": TITLE_STAGE, "blocks": blocks},
        {
            "title": TITLE_COMMAND,
            "blocks": [_action(3, "지휘권자", ["commander", "area"], anchors)],
        },
    ]
    do_not_state = [w["field"] for w in s1["withheld"]]  # 4
    used = [a for sec in sections for b in sec["blocks"] for a in b["anchors"]]
    anchor_basis = _unique([ANCHOR_IDS[a] for a in used])  # 5
    return {"sections": sections, "do_not_state": do_not_state, "anchor_basis": anchor_basis}
