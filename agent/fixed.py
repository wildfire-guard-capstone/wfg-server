# ruff: noqa: E501  (고정문 틀은 문장 단위로 둔다)
"""판정 값으로 바로 쓰는 문단(조립기 고정문): S1 전부 + 목록 · 표 바로 앞 문단.

의성 데모(11/8)에서 EXAONE이 판정을 거꾸로 쓰거나(「산림청장과 협의하지 않습니다」), 아직 넘지 않은
1,000 ha 인계를 이미 된 것처럼 쓰거나, 도지사에게 군수 권한인 대피명령을 직접 내리라고 썼다.
이런 문장은 숫자 · 이름 검사로 걸러지지 않으므로, 판정 칸만으로 정해지는 문단은 여기서 틀을 채운다.
- 문장 꼴은 11/7 목표 문단(LLM-출력-예시.json)을 따른다
- 근거 표기는 그 action의 anchors 안에서만 고른다. 틀에 맞는 근거가 anchors에 없으면 None(LLM에 맡김)
- 읽는 사람이 시 · 도지사 이상이면 시장 · 군수 권한(대피명령 · 위험구역 · 대피소)은 「○○군수에게 … 지시하십시오」
  (재난안전법 제40 · 41조는 시장 · 군수 · 구청장 권한, 제46조②로 시 · 도지사가 지시)
"""

import re
from typing import Any

FULL = {"초기 대응": "초기 대응", "1단계": "확산대응 1단계", "2단계": "확산대응 2단계"}
CHILDREN = "어린이·노인·장애인·환자"


# ── 표기 ──────────────────────────────────────────────────────────────────


def _num(v: float) -> str:
    return f"{v:,.1f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}"


def _josa(word: str, pair: tuple[str, str]) -> str:
    """받침이 있으면 pair[0](이 · 은 · 을), 없으면 pair[1](가 · 는 · 를)."""
    ch = word[-1]
    has = "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0
    return word + (pair[0] if has else pair[1])


def _ro(word: str) -> str:
    """조사 「(으)로」: 받침이 있고 ㄹ이 아니면 「으로」(2동으로, 산불로, 48.5 ha로)."""
    ch = word[-1]
    if "가" <= ch <= "힣":
        fin = (ord(ch) - 0xAC00) % 28
        return word + ("으로" if fin not in (0, 8) else "로")
    return word + "로"


def head_of(sigungu: str) -> str:
    """시 · 군 · 구 → 단체장 직함(의성군 → 의성군수, 안동시 → 안동시장, 동구 → 동구청장)."""
    if sigungu.endswith("군"):
        return sigungu + "수"
    if sigungu.endswith("구"):
        return sigungu + "청장"
    return sigungu + "장"


def _imp(stem: str) -> str:
    """명령형: 「설정」 → 설정하십시오, 「대피시키」 → 대피시키십시오."""
    return stem + ("십시오" if stem.endswith("시키") else "하십시오")


def _purp(stem: str) -> str:
    """목적형: 「설정」 → 설정하도록, 「대피시키」 → 대피시키도록."""
    return stem + ("도록" if stem.endswith("시키") else "하도록")


def upper_reader(reader: str) -> bool:
    """읽는 사람이 시 · 도지사 · 산림청장이면 True(대피명령 등은 시장 · 군수에게 지시)."""
    return bool(re.search(r"도지사|특별시장|광역시장|특별자치|산림청장", reader))


def _cite(block: dict[str, Any], want: list[str]) -> str | None:
    """원하는 근거 중 anchors에 있는 것만, [p.75·76, 시행령 제21조①] 꼴로."""
    have = [a for a in want if a in (block.get("anchors") or [])]
    if not have:
        return None
    pages = [a[2:] for a in have if a.startswith("p.")]
    rest = [a for a in have if not a.startswith("p.")]
    parts = (["p." + "·".join(pages)] if pages else []) + rest
    return "[" + ", ".join(parts) + "]"


def _heads(common: dict[str, Any], item: dict[str, Any]) -> str:
    """지시받을 단체장들: 산불 시군 + 목록 마을이 속한 다른 시군(의성 밖 읍면 단위 마을의 읍면 칸 = 시군)."""
    names = [common.get("incident", {}).get("sigungu") or "의성군"]
    facts = item.get("facts") or {}
    for key in ("immediate", "pre_evac"):
        for r in facts.get(key) or []:
            emd = r.get("emd") or ""
            if re.search(r"(시|군|구)$", emd) and emd not in names:
                names.append(emd)
    return "·".join(head_of(n) for n in names)


def _order(stem: str, cite: str, common: dict, item: dict) -> str:
    """읽는 사람이 시장 · 군수면 「…하십시오」, 위면 「○○군수에게 …하도록 지시하십시오」."""
    if not upper_reader(common.get("reader", "")):
        return f"{_imp(stem)} {cite}."
    return f"{_heads(common, item)}에게 {_purp(stem)} 지시하십시오 {cite}."


# ── S1 ───────────────────────────────────────────────────────────────────


def _s1_a1(item: dict, block: dict, common: dict) -> dict | None:
    f = item["facts"]
    now, change = f["now"], f["change"]
    c73, c91 = _cite(block, ["p.73"]), _cite(block, ["p.91"])
    if not c73 or not c91:
        return None
    fac = (
        f"시설피해 우려 {'최소 ' if now.get('facility_is_minimum') else ''}{_num(now['facility'])}동"
        if now["facility"]
        else "시설피해 우려 없음"
    )
    parts = [
        ("피해면적", f"{_num(now['area_ha'])} ha", now["by_area"]),
        ("평균풍속", f"{_num(now['wind_ms'])} m/s", now["by_wind"]),
        ("", fac, now["by_facility"]),
    ]
    stage = FULL[now["stage"]]
    if len({p[2] for p in parts}) == 1:
        head = (
            f"피해면적 {parts[0][1]}, 평균풍속 {parts[1][1]}, {_ro(fac)} 세 기준 모두 "
            f"판정 기준상 {stage}에 해당합니다 {c73}."
        )
    else:
        each = ", ".join(
            f"{(n + ' ') if n else ''}{_josa(v, ('은', '는'))} {FULL[s]}" for n, v, s in parts
        )
        head = f"{each} 기준이어서 판정 기준상 가장 높은 {stage}에 해당합니다 {c73}."
    if now["stage"] == "초기 대응":
        tail, cat = f"관할 진화자원으로 초동 진화를 이어 가십시오 {c73}.", "즉시"
    elif change["type"] == "same":
        tail = f"직전 제안서와 같은 단계입니다. 아직 발령되지 않았다면 {stage} 발령을 산림청장과 협의하십시오 {c91}."
        cat = "협의"
    elif change["type"] == "down":
        tail = f"직전 제안서({FULL[change['prev']]})보다 낮아졌으므로 단계 조정을 산림청장과 협의하십시오 {c91}."
        cat = "협의"
    else:  # new · up
        tail, cat = f"{stage} 발령을 산림청장과 협의하십시오 {c91}.", "협의"
    return {"action": block["id"], "category": cat, "text": f"{head} {tail}"}


def _s1_a2(item: dict, block: dict, common: dict) -> dict | None:
    f = item["facts"]
    p5, area = f["p5"], f.get("area") or {}
    c73, c91 = _cite(block, ["p.73"]), _cite(block, ["p.91"])
    if not c73 or not c91 or not p5.get("decided_by"):
        return None
    stage = FULL[p5["stage"]]
    slices = area.get("slices") or []
    when = f"5시간 뒤({slices[4]['until']})" if len(slices) >= 5 else "5시간 뒤"
    head = f"{when}에는 {_ro(', '.join(p5['decided_by']))} 판정 기준상 {stage}에 이를 수 있습니다 {c73}."
    thr = {"1단계": 10, "2단계": 100}.get(p5["stage"])
    over = next((o for o in area.get("first_over") or [] if o["ha"] == thr), None)
    mid = (
        f" 피해면적은 {over['until']}까지 {_num(over['area_ha'])} ha로 {thr} ha를 넘을 수 있습니다."
        if over
        else ""
    )
    tail = f" 그 전에 {stage} 격상을 산림청장과 협의하십시오 {c91}."
    return {"action": block["id"], "category": "대기", "text": head + mid + tail}


def _s1_a3(item: dict, block: dict, common: dict) -> dict | None:
    f = item["facts"]
    cmd, area = f["commander"], f.get("area") or {}
    now, nxt, reader = (
        cmd["now"]["title"],
        cmd["next"]["title"],
        common.get("reader") or cmd.get("reader"),
    )
    c72 = _cite(block, ["p.72", "p.114", "시행령 제21조①"])
    c21 = _cite(block, ["시행령 제21조②"])
    if not c72:
        return None
    why_now = "·".join(r["text"] for r in cmd["now"]["reasons"])
    head = (
        f"{_ro(why_now)} 현재 지휘권자는 {now}입니다."
        if why_now
        else f"현재 지휘권자는 {now}입니다."
    )
    if now == nxt:
        return {
            "action": block["id"],
            "category": "즉시",
            "text": f"{head} 5시간 안에 지휘권자가 바뀔 판정은 없으므로 지금 지휘 체계를 유지하십시오 {c72}.",
        }
    reasons = cmd["next"]["reasons"]
    cond = "·".join(r["text"] for r in reasons)
    thr = next((r.get("ha") for r in reasons if r["kind"] == "area"), None)
    over = next((o for o in area.get("first_over") or [] if o["ha"] == thr), None)
    when = (
        f" 피해면적은 {over['until']}까지 {_num(over['area_ha'])} ha로 그 기준을 넘을 수 있습니다."
        if over
        else ""
    )
    mid = (
        f" {_josa(cond, ('이', '가'))} 되면 {_josa(nxt, ('이', '가'))} 통합지휘합니다 {c72}.{when}"
    )
    if reader == now and c21:
        tail = f" 넘는 즉시 {nxt}에게 알리고 상황 정보를 인계하십시오 {c21}."
        cat = "대기"
    elif reader == nxt and c21:
        tail = (
            f" 넘으면 {_ro(now)}부터 상황 정보를 인계받아 통합지휘할 수 있도록 준비하십시오 {c21}."
        )
        cat = "대기"
    else:
        tail, cat = f" 지휘권 변경에 대비하십시오 {c72}.", "대기"
    return {"action": block["id"], "category": cat, "text": head + mid + tail}


# ── 목록 · 표 앞 문단 ───────────────────────────────────────────────────────


def _e2_a2(item: dict, block: dict, common: dict) -> dict | None:
    f = item["facts"]
    c = _cite(block, ["p.75"])
    if not c:
        return None
    night = any(r.get("night") for r in f.get("pre_evac") or []) and not f.get("now_is_night")
    if night and f.get("sunset"):
        stem = (
            f"아래 마을은 밤에 불이 닿을 수 있으므로 고령자를 일몰({f['sunset']}) 전에 사전대피시키고, "
            "나머지 주민에게는 대피소·경로 확인과 비상용품 확보를 안내"
        )
    else:
        stem = (
            "아래 마을 주민에게 대피소·경로 확인과 비상용품 확보를 안내하고, 고령자는 사전대피시키"
        )
    return {"action": block["id"], "category": "대기", "text": _order(stem, c, common, item)}


def _simple(want: list[str], stem: str, cat: str, order: bool, see: str | None = None):
    """stem = 끝 동사 앞까지(「…를 설정」 · 「…부터 대피시키」). order = 시장 · 군수 권한(위면 지시)."""

    def make(item: dict, block: dict, common: dict) -> dict | None:
        c = _cite(block, want)
        if not c or any(a not in (block.get("anchors") or []) for a in want):
            return None
        text = _order(stem, c, common, item) if order else f"{_imp(stem)} {c}."
        if see and block.get("see") == see:
            text += f" 세부 내용은 {see} 참조."
        return {"action": block["id"], "category": cat, "text": text}

    return make


TEMPLATES = {
    "S1-a1": _s1_a1,
    "S1-a2": _s1_a2,
    "S1-a3": _s1_a3,
    "S2-a1": _simple(["p.77"], "진화 우선순위에 따라 아래 순서로 방어하고 보호", "즉시", False),
    "S6-a1": _simple(
        ["p.22", "p.73"], "산불현장 대책회의를 열어 아래 기관별로 임무를 부여", "즉시", False
    ),
    "E1-a1": _simple(["p.75"], "리 단위로 아래와 같이 구역을 설정", "즉시", True),
    "E2-a1": _simple(
        ["p.75", "p.76", "p.79"],
        f"아래 시각까지 대피를 마치도록 마을 단위 대피명령을 내리고, 마을마다 {CHILDREN}부터 대피시키",
        "즉시",
        True,
    ),
    "E2-a2": _e2_a2,
    "E3-a1": _simple(
        ["p.79", "p.84"], "아래 시각 전에 대피를 마치도록 시설별로 우선 대피시키", "즉시", True
    ),
    "E3-a3": _simple(["p.75"], "아래 시설에 사전대피와 이송 준비를 지시", "대기", False),
    "E4-a1": _simple(["p.79"], "위험구역 마을의 대피소를 아래와 같이 지정하고 안내", "즉시", True),
    "E4-a2": _simple(["p.75"], "잠재 위험구역 마을의 대피소를 아래와 같이 미리 지정", "대기", True),
    "E5-a1": _simple(
        ["p.76", "p.78"],
        "대피명령과 함께 아래 지역에 재난문자(CBS)와 자막방송(DITS) 송출을 요청",
        "요청",
        False,
        see="E6",
    ),
}


def paragraph(item: dict[str, Any], block: dict[str, Any], common: dict[str, Any]) -> dict | None:
    """틀이 있으면 고정문 문단, 없거나 근거가 맞지 않으면 None(LLM이 쓴다)."""
    make = TEMPLATES.get(block["id"])
    if make is None:
        return None
    try:
        p = make(item, block, common)
    except (KeyError, IndexError, TypeError):  # 판정 칸 모양이 다르면 LLM에 맡긴다
        return None
    return p | {"source": "fixed"} if p else None
