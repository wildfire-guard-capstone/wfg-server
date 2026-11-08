# ruff: noqa: E501  (문장 틀은 한 줄로 둔다)
"""판정 값 상황 문장 조립(11/8 밤 사용자 결정): 규칙 엔진 판정(facts)으로 주제별 「상황 문장」을 코드가 쓴다.

LLM은 판정 값을 옮겨 쓰다 근거의 기준값으로 바꿔 쓰곤 했다(tune11: 1단계 지상 범위 「경상북도 100%」, 「최소 20동」).
그래서 값이 들어가는 상황 문장은 여기서 판정 그대로 만들고, LLM은 그 뒤에 붙는 「할 일 문장」만 근거 검색으로 쓴다.

    sit = sentences(item)        # {주제: 상황 문장} — 문장을 만들 수 없는 주제는 빠진다(그 주제는 LLM이 전부 씀)
    sit["현재 기준 단계"]          # 「피해면적 48.5 ha, 평균풍속 3.4 m/s, 시설피해 우려 최소 1동으로 세 기준 모두 판정 기준상 확산대응 1단계에 해당합니다.」

대상: S1(현재 기준 단계 · 5시간 뒤 단계 · 지휘권자) · S3(투입 자원 · 현재 단계 동원 범위 · 5시간 뒤 동원 범위) ·
S2(주택 보호 배치 · 송전시설 보호 · 문화유산 이동 · 경비). S2 진화 우선순위는 값이 없어 LLM이 쓴다.
규칙 엔진 facts 구조가 바뀌면 여기와 tests/unit/test_agent_situate.py를 함께 고친다.
"""

from collections.abc import Callable
from typing import Any

STAGE_ORDER = {"초기대응": 0, "1단계": 1, "2단계": 2, "3단계": 3}


def stage_label(s: str | None) -> str:
    if not s:
        return ""
    return s if s == "초기대응" else f"확산대응 {s}"


def _num(x: Any) -> str:
    """48.5 → 48.5, 723.0 → 723, 100 → 100."""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _join(names: list[str]) -> str:
    return " · ".join(n for n in names if n)


_DIGIT_BATCHIM = {
    "0": True,
    "1": True,
    "2": False,
    "3": True,
    "4": False,
    "5": False,
    "6": True,
    "7": True,
    "8": True,
    "9": False,
}


def _batchim(word: str) -> bool:
    """끝 글자에 받침이 있는가(조사 고르기). 괄호 · 공백은 건너뛰고, ha(헥타르) · m/s(초) · km(킬로미터) · %(퍼센트)는 받침 없음."""
    w = word.rstrip()
    while w.endswith(")"):
        w = w[: w.rfind("(")].rstrip() if "(" in w else w[:-1]
    if w.endswith(("ha", "m/s", "km", "%")):
        return False
    ch = w[-1:] if w else ""
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return _DIGIT_BATCHIM.get(ch, False)


def josa(word: str, with_b: str, without_b: str) -> str:
    return word + (with_b if _batchim(word) else without_b)


# ── S1 ─────────────────────────────────────────────────────────────


def _criteria(st: dict[str, Any]) -> list[tuple[str, str]]:
    """(기준 글, 그 기준의 단계). 값이 없는 기준은 뺀다."""
    out = []
    if st.get("area_ha") is not None:
        out.append((f"피해면적 {_num(st['area_ha'])} ha", st.get("by_area")))
    if st.get("wind_ms") is not None:
        out.append((f"평균풍속 {_num(st['wind_ms'])} m/s", st.get("by_wind")))
    if st.get("facility") is not None:
        n = st["facility"]
        txt = (
            "시설피해 우려 없음"
            if n == 0
            else f"시설피해 우려 {'최소 ' if st.get('facility_is_minimum') else ''}{_num(n)}동"
        )
        out.append((txt, st.get("by_facility")))
    return out


def s1_now(f: dict[str, Any]) -> str | None:
    st = f.get("now") or {}
    crit = _criteria(st)
    if not crit or not st.get("stage"):
        return None
    stage = stage_label(st["stage"])
    if all(s == st["stage"] for _, s in crit):
        both = "세 기준 모두" if len(crit) == 3 else "모든 기준이"
        out = f"{josa(', '.join(t for t, _ in crit), '으로', '로')} {both} 판정 기준상 {stage}에 해당합니다."
    else:  # 기준마다 단계가 다르면 높은 단계부터 묶어서: 「피해면적 183.4 ha는 확산대응 2단계, 평균풍속 4.9 m/s와 … 8동은 확산대응 1단계 기준으로」
        groups: dict[str, list[str]] = {}
        for t, s in crit:
            groups.setdefault(s or "", []).append(t)
        parts = []
        for s, ts in sorted(groups.items(), key=lambda kv: -STAGE_ORDER.get(kv[0], -1)):
            if not s:
                continue
            joined = (
                ts[0]
                if len(ts) == 1
                else ", ".join(ts[:-2] + [josa(ts[-2], "과", "와") + " " + ts[-1]])
            )
            parts.append(f"{josa(joined, '은', '는')} {stage_label(s)}")
        out = f"{', '.join(parts)} 기준으로, 판정 기준상 {stage}에 해당합니다."
    ch = f.get("change") or {}
    if ch.get("type") in ("up", "down") and ch.get("prev"):
        out += f" 직전 회차 {stage_label(ch['prev'])}에서 {'올랐' if ch['type'] == 'up' else '내려갔'}습니다."
    return out


def _slice(f: dict[str, Any], p: int) -> dict[str, Any] | None:
    return next((s for s in (f.get("area") or {}).get("slices") or [] if s.get("p") == p), None)


def s1_p5(f: dict[str, Any]) -> str | None:
    p5 = f.get("p5") or {}
    if not p5.get("stage"):
        return None
    stage = stage_label(p5["stage"])
    s5 = _slice(f, 5)
    over = (f.get("area") or {}).get("first_over") or []
    if p5.get("by_area") == p5["stage"] and over:
        o = max(over, key=lambda x: x.get("ha") or 0)
        when = f"{o['until']}에 {_num(o['area_ha'])} ha"
        if s5 and s5.get("until") != o.get("until"):
            when += f", {s5['until']}에 {_num(s5['ha'])} ha"
        return f"피해면적이 {when}로 {stage} 기준 {_num(o['ha'])} ha를 넘을 수 있습니다."
    if p5.get("by_area") == p5["stage"] and s5:
        return f"5시간 뒤({s5['until']}) 피해면적 {_num(s5['ha'])} ha로 판정 기준상 {stage}에 해당할 수 있습니다."
    if p5.get("by_wind") == p5["stage"] and p5.get("wind_ms") is not None:
        return f"5시간 안 평균풍속 {_num(p5['wind_ms'])} m/s로 판정 기준상 {stage}에 해당할 수 있습니다."
    if p5.get("by_facility") == p5["stage"] and p5.get("facility") is not None:
        mn = "최소 " if p5.get("facility_is_minimum") else ""
        return f"5시간 안 시설피해 우려 {mn}{_num(p5['facility'])}동으로 판정 기준상 {stage}에 해당할 수 있습니다."
    return None


def s1_commander(f: dict[str, Any]) -> str | None:
    cm = f.get("commander") or {}
    now, nxt = (cm.get("now") or {}).get("title"), (cm.get("next") or {}).get("title")
    if not now:
        return None
    if not nxt or nxt == now:
        why = [r.get("text") for r in (cm.get("now") or {}).get("reasons") or [] if r.get("text")]
        return (
            f"{why[0]}이므로 현재 지휘권자는 {now}입니다."
            if why
            else f"현재 지휘권자는 {now}입니다."
        )
    out = f"현재 지휘권자는 {now}입니다."
    reasons = (cm.get("next") or {}).get("reasons") or []
    area = next((r for r in reasons if r.get("kind") == "area" and r.get("ha")), None)
    if area:
        over = [
            o for o in (f.get("area") or {}).get("first_over") or [] if o.get("ha") == area["ha"]
        ]
        when = f" {over[0]['until']} 무렵" if over else ""
        out += f" 피해면적이{when} {_num(area['ha'])} ha를 넘으면 대형산불이 되어 {josa(nxt, '이', '가')} 통합지휘합니다."
    elif reasons and reasons[0].get("text"):
        out += f" {reasons[0]['text']}에 해당하면 {josa(nxt, '이', '가')} 통합지휘합니다."
    else:
        out += f" 다음 지휘권자는 {nxt}입니다."
    return out


# ── S3 ─────────────────────────────────────────────────────────────


def _by(group: dict[str, Any]) -> str:
    parts = [f"{b['label']} {_num(b['n'])}" for b in group.get("by") or [] if b.get("n")]
    return f"({', '.join(parts)})" if parts else ""


def s3_deployed(f: dict[str, Any]) -> str | None:
    d = f.get("deployed") or {}
    parts = []
    for key, unit, name in (
        ("helicopter", "대", "헬기"),
        ("crew", "명", "인력"),
        ("vehicle", "대", "장비"),
    ):
        g = d.get(key) or {}
        if g.get("total"):
            parts.append(f"{name} {_num(g['total'])}{unit}{_by(g)}")
    return f"현재 {josa(', '.join(parts), '이', '가')} 투입되어 있습니다." if parts else None


def _ground(g: dict[str, Any]) -> str:
    home, extra = g.get("home") or {}, g.get("extra") or {}
    if extra.get("kind") == "sido" and extra.get("share") == "100%":
        return f"{_join(extra.get('names') or [])} 100%"
    if extra.get("kind") == "national":
        return "전국"
    out = f"{home.get('name')} {home.get('share')}" if home.get("name") else ""
    if extra.get("names"):
        names = ", ".join(extra["names"])
        label = f"인접 시군({names})" if extra.get("kind") == "neighbors" else names
        out += f"{'와 ' if out else ''}{label} {extra.get('share') or ''}".rstrip()
    return out


def _air(a: dict[str, Any]) -> str:
    if a.get("kind") == "national":
        return "전국"
    if a.get("kind") == "radius" and a.get("km"):
        return f"발화지 반경 {_num(a['km'])} km"
    return ""


def s3_scope_now(f: dict[str, Any]) -> str | None:
    s = ((f.get("scope") or {}).get("now")) or {}
    ground, air = _ground(s.get("ground") or {}), _air(s.get("air") or {})
    if not s.get("stage") or not (ground or air):
        return None
    body = ", ".join(
        x for x in (f"지상이 {ground}" if ground else "", f"공중이 {air}" if air else "") if x
    )
    return f"{stage_label(s['stage'])} 동원 범위는 {body}입니다."


def s3_scope_p5(f: dict[str, Any]) -> str | None:
    s = ((f.get("scope") or {}).get("p5")) or {}
    ground, air = _ground(s.get("ground") or {}), _air(s.get("air") or {})
    if not s.get("stage") or not (ground or air):
        return None
    body = ", ".join(
        x for x in (f"지상은 {ground}" if ground else "", f"공중은 {air}" if air else "") if x
    )
    return f"5시간 뒤 {josa(stage_label(s['stage']), '이', '가')} 되면 동원 범위가 {josa(body, '으로', '로')} 넓어집니다."


# ── S2 ─────────────────────────────────────────────────────────────


def s2_houses(f: dict[str, Any]) -> str | None:
    h = f.get("houses") or {}
    rows = [r for r in h.get("in_p5") or [] if r.get("houses")]
    if not rows:
        return None
    mn = "최소 " if "최소" in (h.get("count_basis") or "") else ""
    each = ", ".join(r["village"] + " " + mn + _num(r["houses"]) + "동" for r in rows)
    return f"5시간 범위 안의 주택은 {each}입니다."


def _reach(r: dict[str, Any]) -> str:
    if r.get("zone") == "burning":
        return "이미 불이 닿았습니다"
    if r.get("from"):
        return f"{r['from']}부터 불이 닿을 수 있습니다"
    if r.get("arrival"):
        return f"{r['arrival']}까지 불이 닿을 수 있습니다"
    return "불이 닿을 수 있습니다"


def s2_power(f: dict[str, Any]) -> str | None:
    out = []
    for p in f.get("power") or []:
        reach = (
            "이미 불이 닿았고"
            if p.get("zone") == "burning"
            else (f"{p['from']}부터 불이 닿을 수 있고" if p.get("from") else "불이 닿을 수 있고")
        )
        s = f"{p['name']}에 {reach}"
        if p.get("km_p5"):
            s += f", 5시간 범위 안 길이는 {_num(p['km_p5'])} km입니다."
            if p.get("km_p8") and p["km_p8"] != p["km_p5"]:
                adds = f" {_join(p.get('p8_adds') or [])} 구간을 포함해" if p.get("p8_adds") else ""
                s += f" {p.get('p8_until') or '8시간 안'}까지{adds} {_num(p['km_p8'])} km로 늘어납니다."
        elif p.get("km_p8"):
            s += f", 8시간 범위 안 길이는 {_num(p['km_p8'])} km입니다."
        else:
            s = s.removesuffix("고") + "습니다."
        out.append(s)
    for st in f.get("substations") or []:
        out.append(f"{st['name']}({st.get('kind') or '변전소'})에 {_reach(st)}.")
    return " ".join(out) or None


def s2_heritage(f: dict[str, Any]) -> str | None:
    items = list(f.get("temple") or []) + list((f.get("heritage") or {}).get("items") or [])
    out = []
    for r in items:
        where = _join([r.get("emd") or "", r.get("village") or ""]).replace(" · ", " ")
        tag = ", ".join(x for x in (where, r.get("kind") or "") if x)
        out.append(f"{r['name']}({tag})에 {_reach(r)}." if tag else f"{r['name']}에 {_reach(r)}.")
    return " ".join(out) or None


BUILDERS: dict[tuple[str, str], Callable[[dict[str, Any]], str | None]] = {
    ("S1", "현재 기준 단계"): s1_now,
    ("S1", "5시간 뒤 단계"): s1_p5,
    ("S1", "지휘권자"): s1_commander,
    ("S2", "주택 보호 배치"): s2_houses,
    ("S2", "송전시설 보호"): s2_power,
    ("S2", "문화유산 이동 · 경비"): s2_heritage,
    ("S3", "투입 자원"): s3_deployed,
    ("S3", "현재 단계 동원 범위"): s3_scope_now,
    ("S3", "5시간 뒤 동원 범위"): s3_scope_p5,
}


def sentences(item: dict[str, Any], topics: list[str] | None = None) -> dict[str, str]:
    """{주제: 상황 문장}. topics를 주면 그 주제만(이번 항목의 다룰 주제). 만들다 실패한 주제는 빼고 LLM에 맡긴다."""
    f = item.get("facts") or {}
    out = {}
    for (iid, topic), fn in BUILDERS.items():
        if iid != item.get("id") or (topics is not None and topic not in topics):
            continue
        try:
            s = fn(f)
        except (KeyError, TypeError, ValueError):
            s = None
        if s:
            out[topic] = s
    return out
