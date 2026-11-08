# ruff: noqa: E501
"""근거 청크 → 근거 표기. LLM은 청크 ID만 고르고, 표기는 여기서 만든다(표기 형식 오류 · 없는 근거 방지).

    SM-p079-s03                → p.79
    DEC-FDPA-a21-h01-02        → 시행령 제21조①·②
    REG-ROLE-a8                → 규정 제8조
    REG-ROLE-t3-p2             → 규정 별표 3
    NOTI-MOBL-t0-p1            → 고시 별표
    LAW-DSMA-a38_2             → 재난안전법 제38조의2

render([...]) → 「[p.72·114, 시행령 제21조①·②]」(쪽은 한데 묶고 나머지는 처음 나온 순서)
"""

import re
from typing import Any

# 팀 문서 · 규칙 엔진 anchors와 같은 약칭(시행령 · 규정 · 고시). 나머지는 문서 이름을 그대로 쓴다
SHORT = {
    "SM": "표준매뉴얼",
    "LAW-FDPA": "산림재난방지법",
    "DEC-FDPA": "시행령",
    "RUL-FDPA": "시행규칙",
    "LAW-DSMA": "재난안전법",
    "DEC-DSMA": "재난안전법 시행령",
    "RUL-DSMA": "재난안전법 시행규칙",
    "REG-ROLE": "규정",
    "NOTI-MOBL": "고시",
}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
_ID = re.compile(
    r"^(?P<doc>[A-Z0-9]+-[A-Z0-9]+)-(?:a(?P<art>\d+)(?:_(?P<sub>\d+))?|t(?P<tab>\d+))"
    r"(?:-h(?P<h1>\d+)(?:-(?P<h2>\d+))?)?"
)


def doc_short(chunk: dict[str, Any]) -> str:
    return SHORT.get(chunk.get("doc_id") or "", chunk.get("doc_name") or chunk.get("doc_id") or "")


def label(chunk: dict[str, Any]) -> str:
    """청크 하나의 표기(쪽 · 조 · 별표)."""
    cid = chunk["chunk_id"]
    if chunk.get("doc_id") == "SM" or cid.startswith("SM-"):
        page = chunk.get("page") or re.match(r"SM-p(\d+)", cid).group(1)
        return f"p.{int(page)}"
    m = _ID.match(cid)
    if not m:
        return f"{doc_short(chunk)} {chunk.get('section') or ''}".strip()
    doc = SHORT.get(m["doc"], chunk.get("doc_name") or m["doc"])
    if m["tab"] is not None:
        return f"{doc} 별표" + (f" {int(m['tab'])}" if int(m["tab"]) else "")
    art = f"제{int(m['art'])}조" + (f"의{int(m['sub'])}" if m["sub"] else "")
    if m["h1"]:
        lo, hi = int(m["h1"]), int(m["h2"] or m["h1"])
        art += "·".join(CIRCLED[i - 1] for i in range(lo, hi + 1) if 0 < i <= len(CIRCLED))
    return f"{doc} {art}"


def render(chunks: list[dict[str, Any]]) -> str:
    """여러 청크 → 괄호 하나. 쪽은 「p.72·114」로 묶는다."""
    pages, rest = [], []
    for c in chunks:
        lab = label(c)
        if lab.startswith("p."):
            pages.append(lab[2:])
        else:
            rest.append(lab)
    head = ["p." + "·".join(sorted(dict.fromkeys(pages), key=int))] if pages else []
    parts = head + list(dict.fromkeys(rest))
    return f"[{', '.join(parts)}]" if parts else ""
