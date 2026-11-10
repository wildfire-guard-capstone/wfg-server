# ruff: noqa: E501
"""상황 정정(F-30, DB 설계 2.5): 자유 문장 → 정정 가능 항목의 변경안(확인 카드). 저장은 하지 않는다.

1. 해석: LLM(prompt.CORRECTION) — 없거나 실패하면 규칙(정규식). LLM이 낸 수는 입력에 그 수가 있을 때만 받는다.
2. 검사(도구):
   - 대상 찾기: 마을 이름 → villages.id. 없으면 TARGET_NOT_FOUND, 여러 읍면에 같은 이름이면 AMBIGUOUS_TARGET(422)
   - 허용 항목: 마을 대피 완료 · 대피명령 · 사전대피 완료, 투입 수(헬기 · 차량 · 인력, 합계와 기관별), 재난문자 송출
   - 범위: 투입 수는 0 이상. 자원 테이블에 있는 칸(산림청 · 지자체 · 소방 헬기, 진화차 · 소방차)은 대기 + 이 산불
     투입 대수(Context.capacity) 이하 — 넘는 줄은 확인안에서 빼고(ignored), 저장(COR-01) 때 다시 보면 409
     RESOURCE_CAPACITY_EXCEEDED
   - 이미 그 값이면 바꿀 것 없음(ignored)
3. 변경안 = API CorrectionChange 꼴 {target, target_id, item, before, after} + 화면용 label.
   before는 지금 상황(incidents.situation) 값이다. 확인(COR-01) 때 서버 값과 다시 대조한다.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from assistant import prompt
from assistant.context import (
    DEPLOYED_LABELS,
    GROUP_NAMES,
    GROUP_UNITS,
    UNSPECIFIED,
    Context,
    slot_value,
)
from assistant.tools import change_label, josa, num

VILLAGE_ITEMS = {
    "evacuated": "대피 완료",
    "evac_order": "대피명령",
    "pre_evac_done": "사전대피 완료",
}
GROUPS = ("helicopter", "vehicle", "crew")

NOT_ALLOWED_REASON = {
    "stage": "대응단계는 시스템이 화선 면적 · 풍속 · 시설피해로 판정하므로 정정할 수 없습니다. 새 화선을 보고하면 다시 판정됩니다.",
    "weather": "기상은 정정 항목이 아닙니다. 확산 예측을 다시 실행하면 최신 기상이 반영됩니다.",
    "expected_hours": "진화예상시간은 대응단계 판정에 쓰지 않아(MVP 제외) 정정할 수 없습니다.",
    "injury": "부상 · 고립 · 실종은 아직 정정 항목이 아닙니다. 인명 피해는 관계기관 보고 절차로 알려 주십시오.",
    "fire_line": "화선 위치는 정정 항목이 아닙니다. 지도에서 실측 화선을 보고하거나 고쳐 주십시오.",
    "alert_level": "위기경보는 시스템에 저장하지 않는 값이라 정정할 수 없습니다.",
    "other": "정정할 수 있는 항목(마을 대피 완료 · 대피명령 · 사전대피, 진화자원 투입 수, 재난문자 송출)이 아닙니다.",
    "no_village": "어느 마을인지 알 수 없습니다. 「괴산리 대피 완료」처럼 마을 이름을 넣어 다시 입력해 주십시오.",
    "no_count": "투입 수를 읽지 못했습니다. 「헬기 2대 추가」처럼 수를 넣어 다시 입력해 주십시오.",
}

# ── 수 읽기 ────────────────────────────────────────────────────────

KOR_UNITS = {
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "석": 3, "네": 4, "넷": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9,
}  # fmt: skip
KOR_TENS = {"열": 10, "스무": 20, "스물": 20, "서른": 30, "마흔": 40, "쉰": 50}
COUNT = re.compile(
    r"(\d[\d,]*|(?:열|스무|스물|서른|마흔|쉰)?\s?(?:하나|한|둘|두|셋|세|석|넷|네|다섯|여섯|일곱|여덟|아홉)?)\s*(대|명|기)(?![가-힣]{0,1}[원역])"
)


def read_count(word: str) -> int | None:
    w = (word or "").replace(",", "").replace(" ", "")
    if not w:
        return None
    if w.isdigit():
        return int(w)
    total = 0
    for tens, v in KOR_TENS.items():
        if w.startswith(tens):
            total, w = v, w[len(tens) :]
            break
    if w:
        if w not in KOR_UNITS:
            return None
        total += KOR_UNITS[w]
    return total or None


def counts_in(text: str) -> set[int]:
    """입력에 나온 수(숫자 · 한글 수사) — LLM이 낸 count를 대조한다."""
    out = {int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)}
    for m in COUNT.finditer(text):
        n = read_count(m.group(1))
        if n:
            out.add(n)
    return out


# ── 해석 결과 ───────────────────────────────────────────────────────


@dataclass
class Draft:
    """해석한 변화 1건(검사 전)."""

    kind: str  # evacuated · evac_order · pre_evac_done · disaster_sms_sent · helicopter · vehicle · crew
    village: str = ""
    emd: str = ""
    value: bool = True
    count: int | None = None
    mode: str = "add"  # add · remove · set
    group: str = ""  # forest · military … (기관을 말했을 때만)
    text: str = ""


@dataclass
class Parsed:
    drafts: list[Draft] = field(default_factory=list)
    not_allowed: list[dict[str, str]] = field(default_factory=list)  # {kind, text}
    source: str = "rules"  # rules · llm


# ── 규칙 해석 ───────────────────────────────────────────────────────

GROUP_WORDS = {
    "helicopter": r"헬기|헬리콥터|헬리",
    "vehicle": r"차량|소방차|진화차|지휘차|펌프차|물탱크차|장비",
    "crew": r"인력|진화대원|진화대|대원|공무원|소방관|군인|장병|경찰관|인원",
}
AGENCY_WORDS = {
    "helicopter": [
        ("forest", r"산림청"),
        ("local", r"지자체|임차|시군|군청"),
        ("fire", r"소방"),
        ("military", r"군용|군\s|육군|공군|군헬기|군 헬기|국방"),
        ("police", r"경찰"),
        ("unknown", r"미상|소속\s?모를|기관\s?모를"),
    ],
    "vehicle": [
        ("fire_truck", r"소방차"),
        ("forest_vehicle", r"진화차|지휘차"),
        ("other", r"기타"),
    ],
    "crew": [
        ("special_crew", r"진화대"),
        ("forest_officials", r"공무원"),
        ("fire", r"소방"),
        ("military", r"군인|장병|군\s|육군|군부대"),
        ("police", r"경찰"),
        ("other", r"기타|자원봉사"),
    ],
}
# 자원 구절: 「헬기 2대」 · 「소방차 다섯 대」 · 「진화대원 30명」. 앞의 기관 말 · 뒤의 동사는 같은 절에서만
# 본다(_clause_before · _clause_after) — 절을 넘어 「더 왔고 … 철수」의 철수를 앞 구절에 붙이지 않게.
RES = re.compile(
    rf"(?P<word>{'|'.join(GROUP_WORDS.values())})\s*(?P<mid>[가-힣\s]{{0,6}}?)\s*"
    r"(?P<count>\d[\d,]*|(?:열|스무|스물|서른|마흔|쉰)?\s?(?:하나|한|둘|두|셋|세|석|넷|네|다섯|여섯|일곱|여덟|아홉)?)\s*(?P<unit>대|명|기)"
    r"(?=(?:[은는이가을를도로만와과]|으로)?(?![가-힣]))"  # 「2대는 산림청」처럼 조사 하나는 붙어도 된다
)
CLAUSE = re.compile(r"(?:하고|그리고|이고|으며|며|고|는데|인데)\s|[,.;\n]")


def _clause_before(text: str, start: int, width: int = 10) -> str:
    return CLAUSE.split(text[max(0, start - width) : start])[-1]


def _clause_after(text: str, end: int, width: int = 16) -> str:
    return CLAUSE.split(text[end : end + width])[0]


REMOVE = re.compile(r"철수|복귀|빠졌|빠짐|빠져|줄었|줄어|감소|뺐|돌아갔|귀환")
ADD = re.compile(r"추가|더\s|더$|더\s*(?:왔|투입|들어|보냈|배치)|증원|도착|왔|들어왔|늘")
SET = re.compile(
    r"총|모두|전체|현재|지금|투입\s*중|으로\s*(?:늘|줄)|로\s*(?:늘|줄)|돼\s*있|되어\s*있"
)
VILLAGE = re.compile(
    r"(?:(?P<emd>[가-힣]{1,4}[읍면])\s*)?(?P<name>[가-힣]{1,4}?)\s?(?:\d\s?)?리(?=[\s,·와과랑및은는이가도의에를]|$)"
)
NOT_VILLAGE = {
    "일자",
    "마무",
    "잠자",
    "실마",
    "갈무",
    "앞머",
    "뒷머",
    "보따",
    "거",
    "무",
    "처",
    "관",
    "유",
    "논",
    "원",
    "지",
    "수",
    "도",
    "요",
    "나",
    "이",
    "그",
    "저",
    "조",
    "머",
    "꼬",
}
VERBS = [
    ("evac_order", re.compile(r"대피\s*명령\s*(?:을|이)?\s*(?:내렸|내림|발령|했|함|나갔|떨어|내)")),
    (
        "pre_evac_done",
        re.compile(
            r"사전\s*대피\s*(?:를|가|는)?\s*(?:다\s*|모두\s*)?(?:완료|끝|마쳤|마침|했|됐|함|됨)"
        ),
    ),
    (
        "evacuated",
        re.compile(
            r"(?<!사전\s)(?<!사전)대피\s*(?:를|가|는)?\s*(?:다\s*|모두\s*)?(?:완료|끝|마쳤|마침|했|하였|시켰|됐|되었|함|됨)|빠져\s*나|다\s*나갔|모두\s*나왔|피신\s*(?:했|완료)"
        ),
    ),
]
SMS = re.compile(r"(?:재난\s*)?문자\s*(?:를|가|는)?\s*(?:보냈|발송|송출|나갔|했|함|됨)")
NOT_ALLOWED = [
    (
        "stage",
        re.compile(
            r"(?:[123]\s*단계|초기\s*대응|확산\s*대응)\S*\s*(?:가|이|를|로)?\s*(?:발령|격상|선포|올라|됐|되었|내려|하향)|격상\s*(?:됐|되었|했)"
        ),
    ),
    ("alert_level", re.compile(r"위기\s*경보|경계\s*단계|심각\s*단계|주의\s*단계")),
    ("weather", re.compile(r"바람|풍속|풍향|돌풍|비가|비\s*와|기온|습도|소나기")),
    ("expected_hours", re.compile(r"진화\s*(?:예상\s*)?시간|진화까지|진화\s*완료\s*예상")),
    ("injury", re.compile(r"부상|다쳤|사망|고립|갇혔|실종|연락\s*두절")),
    ("fire_line", re.compile(r"화선|불길|불이\s*(?:번졌|넘어|붙)|연소\s*(?:면적|범위)")),
]


def _group_of(kind: str, text: str, word: str) -> str:
    for key, pat in AGENCY_WORDS[kind]:
        if re.search(pat, text) or re.search(pat, word):
            return key
    return ""


def _move_target(kind: str, group: str, after: str) -> str:
    """미상 · 기타 칸 구절 뒤에 다른 기관만 오면(「… 2대는 산림청이었어」) 옮길 칸, 아니면 ""."""
    if group != UNSPECIFIED[kind] or REMOVE.search(after) or ADD.search(after) or SET.search(after):
        return ""
    to = _group_of(kind, after, "")
    return to if to and to != group else ""


def parse_rules(text: str) -> Parsed:
    out = Parsed(source="rules")
    used: list[tuple[int, int]] = []
    for m in RES.finditer(text):
        count = read_count(m.group("count"))
        if count is None:
            continue
        word = m.group("word")
        kind = next(k for k, pat in GROUP_WORDS.items() if re.fullmatch(pat, word))
        before, after = _clause_before(text, m.start()), _clause_after(text, m.end())
        around = before + " " + m.group("mid") + " " + after
        mode = "remove" if REMOVE.search(around) else "set" if SET.search(around) else "add"
        group = _group_of(kind, before + " " + m.group("mid"), word)
        phrase = (before.strip() + " " + m.group(0) + after).strip()
        used.append(m.span())
        to = _move_target(kind, group, after)
        if to:  # 「미상 헬기 2대는 산림청」: 미상 칸에서 빼 산림청 칸에 넣는다(합계는 그대로)
            out.drafts.append(Draft(kind, count=count, mode="remove", group=group, text=phrase))
            out.drafts.append(Draft(kind, count=count, mode="add", group=to, text=phrase))
            continue
        out.drafts.append(Draft(kind, count=count, mode=mode, group=group, text=phrase))
    rest = text
    for a, b in reversed(used):  # 자원 구절을 지운 나머지에서 마을 · 문자 · 처리 불가를 찾는다
        rest = rest[:a] + " " * (b - a) + rest[b:]
    pos = 0
    verb_hits = sorted(
        ((m.start(), m.end(), kind) for kind, pat in VERBS for m in pat.finditer(rest)),
        key=lambda x: x[0],
    )
    taken: list[tuple[int, int]] = []
    for start, end, kind in verb_hits:
        if any(a <= start < b for a, b in taken):
            continue
        taken.append((start, end))
        segment = rest[pos:start]
        names = [
            (v.group("emd") or "", v.group("name") + "리")
            for v in VILLAGE.finditer(segment)
            if v.group("name") not in NOT_VILLAGE
        ]
        for emd, name in names:
            out.drafts.append(
                Draft(kind, village=name, emd=emd, value=True, text=f"{name} {VILLAGE_ITEMS[kind]}")
            )
        if not names:
            out.not_allowed.append(
                {"kind": "no_village", "text": (segment + rest[start:end]).strip()}
            )
        pos = end
    if SMS.search(rest):
        out.drafts.append(Draft("disaster_sms_sent", value=True, text="재난문자 송출"))
    for kind, pat in NOT_ALLOWED:
        m = pat.search(rest)
        if m:
            out.not_allowed.append({"kind": kind, "text": m.group(0)})
    return out


# ── LLM 해석 ────────────────────────────────────────────────────────


def parse_llm(llm: Any, ctx: Context, text: str, trace: dict | None = None) -> Parsed:
    names = sorted({v["name"] for v in ctx.villages if v.get("name")})
    emds = sorted({v["emd"] for v in ctx.villages if v.get("emd")})
    raw = llm.chat(
        prompt.correction_messages(text, names),
        schema=prompt.correction_schema(names, emds),
        max_tokens=900,
        tag="correction",
    )
    data = prompt.loads(raw)
    if trace is not None:
        trace["correction_llm"] = data
    allowed_counts = counts_in(text)
    out = Parsed(source="llm")
    for c in data.get("changes", []):
        kind = c.get("kind")
        if kind not in prompt.CORRECTION_KINDS:
            continue
        if kind in GROUPS:
            n = c.get("count")
            if not isinstance(n, int) or n not in allowed_counts:  # 입력에 없는 수 = 지어낸 수
                out.not_allowed.append({"kind": "no_count", "text": c.get("text", "")})
                continue
            group = c.get("group") or ""
            if group and group not in DEPLOYED_LABELS[kind]:
                group = ""
            out.drafts.append(
                Draft(
                    kind, count=n, mode=c.get("mode") or "add", group=group, text=c.get("text", "")
                )
            )
        elif kind == "disaster_sms_sent":
            out.drafts.append(Draft(kind, value=bool(c.get("value", True)), text=c.get("text", "")))
        else:
            if not c.get("village"):
                out.not_allowed.append({"kind": "no_village", "text": c.get("text", "")})
                continue
            out.drafts.append(
                Draft(
                    kind,
                    village=c["village"],
                    emd=c.get("emd") or "",
                    value=bool(c.get("value", True)),
                    text=c.get("text", ""),
                )
            )
    out.not_allowed += [
        {"kind": n.get("kind", "other"), "text": n.get("text", "")}
        for n in data.get("not_allowed", [])
    ]
    return out


# ── 검사 · 변경안 ───────────────────────────────────────────────────


class CorrectionError(Exception):
    """확인안을 만들 수 없는 입력(422 · 409). code는 API 오류 코드."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def resolve(ctx: Context, name: str, emd: str = "") -> list[dict[str, Any]]:
    """마을 이름 → 후보(villages.id). 「괴산1리」 · 「괴산」도 「괴산리」로 찾는다."""
    base = re.sub(r"\s|\d", "", name or "")
    if base and not base.endswith("리"):
        base += "리"
    zone = ctx.zone_villages()
    zone_ids = {str(v["id"]) for v in zone}
    pool = [
        {"id": v["id"], "name": v["name"], "emd": v.get("emd"), "sigungu": v.get("sigungu")}
        for v in zone
    ] + list(ctx.villages)
    # 같은 이름이 여러 곳이면 ① 이번 제안서의 마을 ② 산불 시군구 ③ 인접 시군구 순으로 좁힌다
    tiers = [
        [v for v in pool if str(v["id"]) in zone_ids],
        [v for v in pool if v.get("home", True)],
        pool,
    ]
    for tier in tiers:
        uniq: dict[str, dict[str, Any]] = {}
        for v in tier:
            if v.get("name") == base and (not emd or v.get("emd") == emd):
                uniq.setdefault(str(v["id"]), v)
        if uniq:
            return list(uniq.values())
    return []


# 기관을 말하지 않은 투입 수를 넣을 칸. 규칙 엔진은 기관별 칸을 더해 합계를 다시 계산하므로(엔진 입력
# deployed_of는 total을 버림) 합계만 바꾸면 제안서에 반영되지 않는다. 헬기는 「미상」, 인력 · 차량은 「기타」
# 칸(UNSPECIFIED)에 넣고, 기관을 알게 되면 「미상 헬기 2대는 산림청」처럼 다시 정정한다(11-10 결정).
AGENCY_HINT = {
    "helicopter": "산림청 · 지자체 · 소방 · 군 · 경찰",
    "vehicle": "소방차 · 진화차",
    "crew": "진화대 · 공무원 · 소방 · 군 · 경찰",
}
MOVE_EXAMPLE = {
    "helicopter": "미상 헬기 2대는 산림청",
    "vehicle": "기타 차량 2대는 소방차",
    "crew": "기타 인력 10명은 소방",
}


def _deployed_change(
    ctx: Context, d: Draft, pending: dict[str, Any]
) -> tuple[list[dict[str, Any]], str | None]:
    """투입 수 변화 1건 → (변경안, 확인 카드에 보일 메모). 줄일 기관을 꼭 알아야 하면 NeedsAgency."""
    group, sub = d.kind, d.group
    note = None
    if not sub:
        sub = UNSPECIFIED[group]
        note = (
            f"{GROUP_NAMES[group]} 기관을 말하지 않아 「{DEPLOYED_LABELS[group][sub]}」 칸에 넣었습니다"
            f"(기관을 알면 「{MOVE_EXAMPLE[group]}」처럼 다시 정정해 주십시오)."
        )
    cur = dict((ctx.situation.get("deployed") or {}).get(group) or {})
    cur.update(pending.get(group, {}))  # 같은 입력 안에서 앞선 변경을 이어 받는다
    total = slot_value(cur, group, "total")
    n = d.count or 0
    before_sub = slot_value(cur, group, sub)  # 미상 · 기타 칸은 합계의 남는 몫까지
    if d.mode == "add":
        delta = n
    elif d.mode == "remove":
        delta = -n
    elif d.group:  # 기관 칸을 n으로
        delta = n - before_sub
    else:  # 합계를 n으로(모르는 기관 몫은 미상 · 기타 칸에서)
        delta = n - total
    after_total, after_sub = total + delta, before_sub + delta
    if after_total >= 0 and after_sub < 0 and not d.group:
        raise NeedsAgency(group)  # 미상 · 기타 칸에 뺄 만큼이 없으면 어느 기관이 줄었는지 묻는다
    if after_total < 0 or after_sub < 0:
        raise CorrectionError(
            422,
            "INVALID_COUNT",
            f"투입 {GROUP_NAMES[group]} 수가 0보다 작아집니다(지금 {num(total)}{GROUP_UNITS[group]}"
            f", 그중 {DEPLOYED_LABELS[group][sub]} {num(before_sub)}). 기관과 수를 확인해 주십시오.",
        )
    problem = over_capacity(f"{group}.{sub}", before_sub, after_sub, ctx.capacity)
    if problem:
        raise OverCapacity(problem)
    if delta == 0:
        return [], None
    out = [
        {"target": "deployed", "item": f"{group}.{sub}", "before": before_sub, "after": after_sub},
        {"target": "deployed", "item": f"{group}.total", "before": total, "after": after_total},
    ]
    pending.setdefault(group, {}).update({sub: after_sub, "total": after_total})
    return out, note


def over_capacity(item: str, before: int, after: int, capacity: dict[str, int]) -> str | None:
    """투입 칸(helicopter.forest 등)이 자원 현황 상한을 넘게 늘면 그 이유, 아니면 None.

    상한 = 자원 테이블의 대기 + 이 산불 투입 대수(app repo.capacity). 줄이는 정정과 상한을 모르는 칸
    (군 · 경찰 · 미상 · 기타 · 인력)은 통과한다. 확인안(AI-01)과 저장(COR-01)이 같이 쓴다.
    """
    cap = capacity.get(item)
    if cap is None or after <= before or after <= cap:
        return None
    group, _, key = item.partition(".")
    unit = GROUP_UNITS[group]
    return (
        f"{DEPLOYED_LABELS[group][key]} {GROUP_NAMES[group]} {num(after)}{unit}는 자원 현황의 가용 대수"
        f"({num(cap)}{unit} = 대기 + 이 산불 투입)를 넘어 반영할 수 없습니다. "
        "수를 확인하시거나 자원 관리자에게 자원 현황 갱신을 요청해 주십시오."
    )


class OverCapacity(Exception):
    """확인안에서는 그 줄만 반영하지 않음(ignored)으로 보인다."""


class NeedsAgency(Exception):
    def __init__(self, group: str):
        super().__init__(group)
        self.group = group


def build_changes(
    ctx: Context, parsed: Parsed
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    """해석 → (변경안, 무시한 것, 메모). 대상을 못 찾으면 CorrectionError."""
    changes: dict[tuple[str, str, str], dict[str, Any]] = {}
    ignored: list[dict[str, str]] = []
    notes: list[str] = []
    pending: dict[str, Any] = {}
    for d in parsed.drafts:
        if d.kind in VILLAGE_ITEMS:
            hits = resolve(ctx, d.village, d.emd)
            if not hits:
                raise CorrectionError(
                    422,
                    "TARGET_NOT_FOUND",
                    f"「{d.village}」 마을을 관할 마을 목록에서 찾지 못했습니다. 마을 이름을 확인해 주십시오.",
                )
            if len(hits) > 1:
                emds = " · ".join(sorted({h.get("emd") or "" for h in hits}))
                raise CorrectionError(
                    422,
                    "AMBIGUOUS_TARGET",
                    f"「{d.village}」가 {emds}에 모두 있습니다. 「안평면 {d.village}」처럼 읍면을 붙여 다시 입력해 주십시오.",
                )
            v = hits[0]
            vid = str(v["id"])
            before = bool(ctx.village_field(vid).get(d.kind, False))
            label = f"{v.get('emd', '')} {v['name']}".strip()
            if before == d.value:
                ignored.append(
                    {
                        "text": d.text or label,
                        "reason": f"{josa(label, '은', '는')} 이미 「{VILLAGE_ITEMS[d.kind]} {'예' if before else '아니오'}」로 기록되어 있습니다.",
                    }
                )
                continue
            ch = {
                "target": "village",
                "target_id": vid,
                "item": d.kind,
                "before": before,
                "after": d.value,
                "target_name": label,
            }
            changes[("village", vid, d.kind)] = ch
        elif d.kind == "disaster_sms_sent":
            before = bool(ctx.situation.get("disaster_sms_sent", False))
            if before == d.value:
                ignored.append(
                    {"text": d.text, "reason": "재난문자 송출은 이미 그렇게 기록되어 있습니다."}
                )
                continue
            changes[("situation", "", d.kind)] = {
                "target": "situation",
                "item": d.kind,
                "before": before,
                "after": d.value,
            }
        elif d.kind in GROUPS:
            try:
                got, note = _deployed_change(ctx, d, pending)
            except NeedsAgency as e:
                ignored.append(
                    {
                        "text": d.text,
                        "reason": f"기관을 모르는 {GROUP_NAMES[e.group]} 수보다 많이 줄어 어느 기관({AGENCY_HINT[e.group]})이 줄었는지 알려 주셔야 반영할 수 있습니다. 예: 「산림청 {GROUP_NAMES[e.group]} 2{GROUP_UNITS[e.group]} 철수」",
                    }
                )
                continue
            except OverCapacity as e:
                ignored.append({"text": d.text, "reason": str(e)})
                continue
            if note and note not in notes:
                notes.append(note)
            if not got:
                ignored.append(
                    {"text": d.text, "reason": "투입 수가 지금과 같아 바꿀 것이 없습니다."}
                )
            for ch in got:
                key = ("deployed", "", ch["item"])
                if key in changes:  # 같은 칸을 두 번 바꾸면 처음 before · 마지막 after
                    ch = {**ch, "before": changes[key]["before"]}
                changes[key] = ch
    for n in parsed.not_allowed:
        ignored.append(
            {
                "text": n.get("text", ""),
                "reason": NOT_ALLOWED_REASON.get(
                    n.get("kind", "other"), NOT_ALLOWED_REASON["other"]
                ),
            }
        )
    # 칸을 옮기면 합계 줄은 처음 · 끝이 같다 → 바뀌지 않은 줄은 뺀다
    out = [c for c in changes.values() if c["target"] != "deployed" or c["before"] != c["after"]]
    for ch in out:
        ch["label"] = change_label(ch)
    return out, ignored, notes


def _stem(name: str) -> str:
    return re.sub(r"\s|\d", "", name or "").removesuffix("리")


def _agency_in_text(kind: str, group: str, text: str) -> bool:
    pats = [p for g, p in AGENCY_WORDS[kind] if g == group]
    return bool(pats) and re.search(pats[0], text + " ") is not None


def merge(llm: Parsed, rules: Parsed, text: str, trace: dict | None = None) -> Parsed:
    """LLM 해석 + 규칙 해석. LLM은 입력에 없는 것을 고를 수 있으므로(11-09 평가: 「박곡리」 → enum에 있는
    「도옥리」, 「군 헬기」 → 지자체) 입력 글로 다시 대조한다.

    - 마을: LLM이 고른 이름(「리」 뺀 줄기)이 입력에 있어야 받는다
    - 투입 수: LLM이 고른 기관 말이 입력에 없으면 규칙이 같은 종류 · 수에서 찾은 기관으로(없으면 기관 없음)
    - LLM이 빠뜨린 변화(규칙만 찾은 것)는 더한다
    - 처리 불가: LLM이 「그 밖」이라 했어도 규칙이 구체 이유(대응단계 · 기상 …)를 찾았으면 그 이유로
    """
    out = Parsed(source="llm+rules")
    dropped: list[str] = []
    # 규칙이 찾은 칸 옮기기(「미상 헬기 2대는 산림청」 → 미상 빼기 + 산림청 더하기)는 규칙 해석을 쓴다.
    # LLM이 한쪽만 내면(산림청 2대 추가) 합계가 늘어나 버린다
    moves = [
        r
        for r in rules.drafts
        if r.kind in GROUPS
        and sum(o.kind == r.kind and o.count == r.count and o.text == r.text for o in rules.drafts)
        == 2
    ]
    moved = {(r.kind, r.count) for r in moves}
    for d in llm.drafts:
        if d.kind in GROUPS and (d.kind, d.count) in moved:
            continue
        if d.kind in VILLAGE_ITEMS:
            stem = _stem(d.village)
            if not stem or stem not in re.sub(r"\s", "", text):
                dropped.append(f"{d.kind}:{d.village}")
                continue
        elif d.kind in GROUPS:
            same = next((r for r in rules.drafts if r.kind == d.kind and r.count == d.count), None)
            if d.group and not _agency_in_text(d.kind, d.group, text):
                dropped.append(f"{d.kind}.{d.group}")
                d.group = same.group if same else ""
            elif not d.group and same and same.group:
                d.group = same.group
        out.drafts.append(d)

    def key(d: Draft) -> tuple:
        if d.kind in VILLAGE_ITEMS:
            return (d.kind, _stem(d.village))
        return (d.kind, d.count) if d.kind in GROUPS else (d.kind,)

    have = {key(d) for d in out.drafts}
    out.drafts += [
        r for r in rules.drafts if key(r) not in have
    ]  # 옮기기 둘은 여기서 함께 들어온다
    specific = [n for n in rules.not_allowed if n["kind"] not in ("other",)]
    kinds = {n["kind"] for n in specific}
    out.not_allowed = specific + [
        n
        for n in llm.not_allowed
        if n.get("kind") not in kinds and not (n.get("kind") == "other" and specific)
    ]
    if trace is not None and dropped:
        trace["correction_llm_dropped"] = dropped
    return out


def preview(
    ctx: Context, text: str, llm: Any | None = None, trace: dict | None = None
) -> dict[str, Any]:
    """정정 입력 → CORRECTION_PREVIEW 또는 UNSUPPORTED. 대상 문제는 CorrectionError."""
    if ctx.ended:
        raise CorrectionError(
            409, "INCIDENT_ENDED", "종료된 산불은 상황을 정정할 수 없습니다(질문은 할 수 있습니다)."
        )
    parsed = rules = parse_rules(text)
    if llm is not None:
        try:
            parsed = merge(parse_llm(llm, ctx, text, trace), rules, text, trace)
        except (ValueError, KeyError, TypeError) as exc:  # JSON이 깨지면 규칙으로
            if trace is not None:
                trace["correction_llm_error"] = str(exc)
    if trace is not None:
        trace["correction_source"] = parsed.source
        trace["drafts"] = [d.__dict__ for d in parsed.drafts]
    changes, ignored, notes = build_changes(ctx, parsed)
    if not changes:
        reasons = list(dict.fromkeys(i["reason"] for i in ignored)) or [NOT_ALLOWED_REASON["other"]]
        return {"type": "UNSUPPORTED", "reason": " ".join(reasons), "ignored": ignored}
    return {
        "type": "CORRECTION_PREVIEW",
        "input_text": text,
        "changes": changes,
        "ignored": ignored,
        "notes": notes,
        "summary": summary(changes),
    }


def summary(changes: list[dict[str, Any]]) -> str:
    """확인 카드 머리 문장. 투입 수는 기관 칸 줄만 센다(합계 줄은 같은 변경의 결과)."""
    shown = [c for c in changes if not (c["target"] == "deployed" and c["item"].endswith(".total"))]
    return (
        f"{josa(f'변경 {len(shown)}건', '을', '를')} 확인하면 상황에 저장하고 제안서를 다시 만듭니다: "
        + " / ".join(c["label"] for c in shown)
    )
