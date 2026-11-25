# ruff: noqa: E501
"""제안 소제목(11/24 BLUF 형식, 제품 C3 전용).

화면 = 제안(소제목 → 핵심 판단 근거 → 제안 → 자세히 보기) → 현황(상황 · 상태 · 파악) → 판단 근거 → 출처 → 누락된 정보.
「제안-프롬프트-개편-계획(20261124).md」 3장 · 사용자 결정(11-24):

- 소제목 이름 · 순서는 코드가 정한다(항목마다 고정, 목업 이름). 소제목이 없는 항목은 UNTITLED 하나.
- 소제목을 보일지: 코드가 먼저 — 규칙 엔진이 그 섹션(「N. …」)을 만들지 않았으면(대상 없음) LLM에 주지 않는다.
  남은 소제목은 LLM이 show(True/False)로 정한다.
- 자세히 보기: 표 · 목록 내용은 여기서 판정 값으로 만든다(LLM은 show_detail True/False만). ※ 안내(note)는 넣지 않는다
  (화면의 「누락된 정보」가 모은다).
- 관련 항목의 일: 요청 표(S6 · E6)의 「항목」 칸으로 이어지는 일. 요청은 원래 항목과 S6 · E6 양쪽 모두 제안할 수 있고,
  함께 할 일이면 「…요청(S6)과 함께」처럼 문장 속에서 말한다(링크 · 「참조」 없음).

프롬프트에 매뉴얼 내용을 넣지 않는다(P1): 「다루는 것」은 범위만 적고 조치 · 기관 · 기준은 적지 않는다.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from agent import calc, judgment

UNTITLED = "제안"  # 소제목이 없는 항목의 parts 키(화면에는 소제목 줄이 없음)
REQUEST_ITEMS = ("S6", "E6")  # 요청 표가 있는 항목
REQ_HEAD = ["관련 기관", "세부 대상(기관명)", "대상", "시각"]
REQ_KEYS = ("group", "agency", "target", "when")


@dataclass(frozen=True)
class Spec:
    title: str
    section: int | None  # 규칙 엔진 섹션 번호(「N. …」). None = 늘 있음
    scope: str  # 다루는 것(범위만 — 조치 · 기관 · 기준을 넣지 않는다)


SPECS: dict[str, list[Spec]] = {
    "S1": [
        Spec("대응단계 판단·격상 검토", 1, "지금과 앞으로의 대응 단계, 단계가 오르는지"),
        Spec("지휘권 인계", 2, "통합지휘권자와 지휘권이 넘어가는 시간대"),
    ],
    "S2": [
        Spec(
            "진화 우선지역·보호대상",
            1,
            "진화할 순서와 보호할 대상(마을 · 취약시설 · 국가기간시설 · 주택)",
        ),
        Spec("시설 보호", 2, "송전선 · 주택 같은 시설 주변의 진화 배치"),
    ],
    "S3": [
        Spec("진화자원 현황", 1, "지금 투입된 헬기 · 인력 · 장비"),
        Spec("동원 범위", 2, "지금 단계의 동원 범위와 부를 수 있는 기지"),
    ],
    "S4": [
        Spec("진화 방식", 2, "헬기 · 지상 진화 방식과 헬기 재투입"),
        Spec("시간대 전략", 1, "일몰 · 밤과 시각별 바람에 따른 진화"),
    ],
    "S5": [
        Spec("진화인력 안전", None, "진화 인력이 처한 위험(확산 방향 · 바람)"),
        Spec("투입 관리", None, "투입 인력의 편성과 인솔"),
    ],
    # 11/25: 대피 카드도 소제목 구조로. 이름은 5시간 안 / 5 ~ 8시간(E3과 같음), E5는 엔진 주제 그대로
    "E1": [
        Spec("위험구역", 1, "위험구역(이미 닿음 · 5시간 안)으로 정할 마을"),
        Spec("잠재 위험구역", 2, "잠재 위험구역(5 ~ 8시간)으로 정할 마을"),
    ],
    "E2": [
        Spec("즉시 대피", 1, "위험구역(5시간 안) 마을 주민의 대피 대상 · 순서 · 시점"),
        Spec("사전대피", 2, "잠재 위험구역(5 ~ 8시간) 마을 주민의 사전대피 대상 · 순서 · 시점"),
    ],
    "E3": [
        Spec("즉시 대피·이송", 1, "위험구역(5시간 안) 취약시설 거주자의 대피"),
        Spec("사전대피·이송 준비", 2, "잠재 위험구역(5 ~ 8시간) 취약시설 거주자의 대피 준비"),
        Spec("이송 요청", 3, "일반 대피소로 갈 수 없는 거주자의 이송"),
    ],
    "E4": [
        Spec("대피소 지정", 1, "마을별 대피소 지정과 수용"),
        Spec("관련 통제", 2, "산불이 닿는 도로의 통제"),
    ],
    "E5": [
        Spec("재난문자·방송", 1, "재난문자와 방송으로 알리는 지역과 단계(Go · Set)"),
        Spec("전파 수단", 2, "재난문자 · 방송과 함께 쓰는 전파 수단"),
    ],
}


@dataclass
class Part:
    title: str
    scope: str
    detail: list[dict[str, Any]] = field(
        default_factory=list
    )  # 자세히 보기 블록(list · table, caption 가능)
    query_words: list[str] = field(default_factory=list)  # 검색 질의에 더할 낱말(관련 기관 등)

    @property
    def untitled(self) -> bool:
        return self.title == UNTITLED


# ── 규칙 엔진 섹션 ─────────────────────────────────────────────────────────
_NUM = re.compile(r"^\s*(\d+)\s*\.")


def section_no(title: str) -> int | None:
    m = _NUM.match(title or "")
    return int(m.group(1)) if m else None


def section_numbers(item: dict[str, Any]) -> set[int]:
    return {
        n for s in item.get("sections", []) if (n := section_no(s.get("title", ""))) is not None
    }


def section_data(item: dict[str, Any], n: int | None = None) -> list[dict[str, Any]]:
    """그 번호 섹션(None = 모든 섹션)의 목록 · 표(※ 안내 제외). 규칙 엔진 블록 꼴 그대로(writer만 뺌)."""
    out = []
    for s in item.get("sections", []):
        if n is not None and section_no(s.get("title", "")) != n:
            continue
        for b in s.get("blocks", []):
            if b.get("type") in ("list", "table"):
                out.append({k: v for k, v in b.items() if k != "writer"})
    return out


def notes_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    """규칙 엔진 ※ 안내(화면 「누락된 정보」로 간다)."""
    return [
        {k: v for k, v in b.items() if k != "writer"}
        for s in item.get("sections", [])
        for b in s.get("blocks", [])
        if b.get("type") == "note"
    ]


# ── 요청 표(S6 · E6) ──────────────────────────────────────────────────────
def request_rows(item: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not item:
        return []
    return [
        r
        for s in item.get("sections", [])
        for b in s.get("blocks", [])
        if b.get("type") == "table"
        for r in b.get("rows", [])
        if isinstance(r, dict) and r.get("agency")
    ]


def request_table(rows: list[dict[str, Any]], caption: str = "") -> list[dict[str, Any]]:
    if not rows:
        return []
    block: dict[str, Any] = {
        "type": "table",
        "head": REQ_HEAD,
        "rows": [[str(r.get(k) or "") for k in REQ_KEYS] for r in rows],
    }
    if caption:
        block["caption"] = caption
    return [block]


def _agencies(rows: list[dict[str, Any]]) -> list[str]:
    """검색 질의에 더할 짧은 기관 이름(띄어 쓴 마을 · 이장 목록과 긴 이름은 뺀다)."""
    out = []
    for r in rows:
        out.append(str(r.get("group") or ""))
        out += [
            a.strip()
            for a in str(r.get("agency") or "").split(",")
            if 0 < len(a.strip()) <= 12 and " " not in a.strip()
        ]
    return [w for w in dict.fromkeys(out) if w]


# ── 판정 값으로 만드는 표 ──────────────────────────────────────────────────
def _hm(at: str) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", at or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def _later(base: str, hours: int = 5) -> str:
    hm = _hm(base)
    if not hm:
        return ""
    t = datetime.strptime(hm, "%H:%M") + timedelta(hours=hours)
    return t.strftime("%H:%M")


def _n(x: Any) -> str:
    return f"{x:,}" if isinstance(x, int) else str(x)


def stage_table(item: dict[str, Any], common: dict[str, Any]) -> list[dict[str, Any]]:
    """S1 대응단계: 판단 요소(면적 · 풍속 · 시설)의 지금 · 5시간 뒤 값과 그 값의 단계."""
    f = item.get("facts") or {}
    now, p5 = f.get("now") or {}, f.get("p5") or {}
    if not now:
        return []
    base = _hm(str((common.get("proposal") or {}).get("base_at") or ""))
    later = _later(base)

    def cell(d: dict[str, Any], key: str, unit: str, by: str, minimum: str = "") -> str:
        if d.get(key) is None:
            return "—"
        pre = "최소 " if minimum and d.get(minimum) else ""
        return f"{pre}{_n(d[key])}{unit}" + (f"({d[by]})" if d.get(by) else "")

    rows = [
        ["피해면적", cell(now, "area_ha", " ha", "by_area"), cell(p5, "area_ha", " ha", "by_area")],
        ["풍속", cell(now, "wind_ms", " m/s", "by_wind"), cell(p5, "wind_ms", " m/s", "by_wind")],
        [
            "주요 시설",
            cell(now, "facility", "동", "by_facility", "facility_is_minimum"),
            cell(p5, "facility", "동", "by_facility", "facility_is_minimum"),
        ],
        ["판정 단계", str(now.get("stage") or "—"), str(p5.get("stage") or "—")],
    ]
    head = [
        "판단 요소",
        f"지금({base})" if base else "지금",
        f"{later}(5시간 뒤)" if later else "5시간 뒤",
    ]
    return [{"type": "table", "head": head, "rows": rows}]


def commander_table(common: dict[str, Any]) -> list[dict[str, Any]]:
    """S1 지휘권 인계: 지휘권자가 넘어가는 구간(RE-1 steps). 넘어가지 않으면 없음."""
    steps = [s for s in (common.get("commander") or {}).get("steps") or [] if s.get("title")]
    if not steps:
        return []
    rows = [[str(s.get("at") or ""), str(s["title"]), str(s.get("why") or "")] for s in steps]
    return [{"type": "table", "head": ["시각", "지휘권자", "이유"], "rows": rows}]


def deployed_table(item: dict[str, Any]) -> list[dict[str, Any]]:
    """S3 진화자원 현황: 헬기 · 인력 · 장비 투입과 내역."""
    d = (item.get("facts") or {}).get("deployed") or {}
    rows = []
    for key, label, unit in (
        ("helicopter", "헬기", "대"),
        ("crew", "인력", "명"),
        ("vehicle", "장비", "대"),
    ):
        x = d.get(key) or {}
        if not x.get("total"):
            continue
        by = " · ".join(f"{b['label']} {_n(b['n'])}" for b in x.get("by") or [] if b.get("n"))
        rows.append([label, f"{_n(x['total'])}{unit}", by])
    return [{"type": "table", "head": ["구분", "투입", "내역"], "rows": rows}] if rows else []


def _ground(s: dict[str, Any]) -> str:
    g = s.get("ground") or {}
    home, extra = g.get("home") or {}, g.get("extra") or {}
    out = f"{home.get('name', '')} {home.get('share', '')}".strip()
    if extra.get("names"):
        out += f" + {', '.join(extra['names'])} {extra.get('share', '')}".rstrip()
    return out or "—"


def _air(s: dict[str, Any]) -> str:
    a = s.get("air") or {}
    if a.get("kind") == "national":
        return "전국"
    return f"발화지 반경 {a.get('km')} km" if a.get("km") is not None else "—"


def mobilization_table(item: dict[str, Any], common: dict[str, Any]) -> list[dict[str, Any]]:
    """S3 동원 범위: 지금 · 5시간 뒤 동원 범위(지상 · 공중)와 지금 부를 수 있는 기지."""
    f = item.get("facts") or {}
    scope = f.get("scope") or {}
    now, p5 = scope.get("now") or {}, scope.get("p5") or {}
    if not now:
        return []
    later = _later(str((common.get("proposal") or {}).get("base_at") or ""))
    bases = ", ".join(
        f"{b['name']}({b['km']} km)" if b.get("km") is not None else b["name"]
        for b in f.get("available_now") or []
        if b.get("name")
    )
    same = (_ground(now), _air(now)) == (_ground(p5), _air(p5)) or not p5
    if same:
        head = [
            "구분",
            f"지금 · {later} 동원 범위" if later else "동원 범위",
            "지금 부를 수 있는 기지(발화지에서 거리)",
        ]
        rows = [["지상", _ground(now), "—"], ["공중", _air(now), bases or "—"]]
    else:
        head = [
            "구분",
            "지금 동원 범위",
            f"{later} 동원 범위" if later else "5시간 뒤 동원 범위",
            "지금 부를 수 있는 기지(발화지에서 거리)",
        ]
        rows = [
            ["지상", _ground(now), _ground(p5), "—"],
            ["공중", _air(now), _air(p5), bases or "—"],
        ]
    return [{"type": "table", "head": head, "rows": rows}]


def wind_table(item: dict[str, Any]) -> list[dict[str, Any]]:
    """S4 시간대 전략: 시각별 풍향 · 풍속(+ 일몰)."""
    f = item.get("facts") or {}
    rows = [
        [str(w.get("at") or ""), str(w.get("dir") or ""), f"{w.get('ms')} m/s"]
        for w in f.get("wind") or []
        if w.get("at")
    ]
    if not rows:
        return []
    block: dict[str, Any] = {"type": "table", "head": ["시각", "풍향", "풍속"], "rows": rows}
    sun = f.get("sun") or {}
    if sun.get("sunset"):
        block["caption"] = f"일몰 {sun['sunset']}" + (
            " · 지금은 밤" if sun.get("now_is_night") else ""
        )
    return [block]


def crew_table(item: dict[str, Any]) -> list[dict[str, Any]]:
    """S5 투입 관리: 인력 구분별 인원과 인솔 기관."""
    f = item.get("facts") or {}
    rows = [[str(b["label"]), f"{_n(b['n'])}명"] for b in f.get("crew_by") or [] if b.get("n")]
    if not rows:
        return []
    if f.get("crew_total"):
        rows.append(["합계", f"{_n(f['crew_total'])}명"])
    block: dict[str, Any] = {"type": "table", "head": ["구분", "인원"], "rows": rows}
    if f.get("leaders"):
        block["caption"] = "인솔 기관: " + " · ".join(f["leaders"])
    return [block]


def alert_log(item: dict[str, Any]) -> list[dict[str, Any]]:
    """E5 문자 이력."""
    log = (item.get("facts") or {}).get("alert_log") or []
    rows = [
        {"text": f"{a.get('village', '')} {a.get('at', '')} {a.get('kind', '')}".strip()}
        for a in log
    ]
    return [{"type": "list", "caption": "문자 이력", "rows": rows}] if rows else []


# ── 항목 → 소제목 ─────────────────────────────────────────────────────────
def _detail(
    iid: str,
    title: str,
    item: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    req = {src: request_rows(by_id.get(src)) for src in REQUEST_ITEMS}
    if iid == "S1":
        return (
            stage_table(item, common)
            if title == "대응단계 판단·격상 검토"
            else commander_table(common)
        )
    if iid == "S2":
        if title == "시설 보호":
            return request_table(
                [r for r in req["S6"] if r.get("item") == "S2"],
                "S6 요청 표 가운데 이 소제목의 대상",
            )
        return section_data(item, 1)
    if iid == "S3":
        return (
            deployed_table(item) if title == "진화자원 현황" else mobilization_table(item, common)
        )
    if iid == "S4":
        return wind_table(item) if title == "시간대 전략" else []
    if iid == "S5":
        return crew_table(item) if title == "투입 관리" else []
    if iid == "E3":
        n = {"즉시 대피·이송": 1, "사전대피·이송 준비": 2}.get(title)
        return section_data(item, n) if n else []
    if iid == "E4":
        if title == "대피소 지정":
            return calc.shelter_table(item) + section_data(item, 1)
        return request_table(
            [r for r in req["E6"] if r.get("item") == "E4"], "E6 요청 표 가운데 이 소제목의 대상"
        )
    if iid in REQUEST_ITEMS:
        return request_table(req[iid])
    if iid == "E5":
        return section_data(item, 1) + alert_log(item) if title == "재난문자·방송" else []
    n = {"위험구역": 1, "잠재 위험구역": 2, "즉시 대피": 1, "사전대피": 2}.get(title)
    return section_data(item, n)  # E1 · E2


def _query_words(iid: str, title: str, by_id: dict[str, dict[str, Any]]) -> list[str]:
    """검색 질의에 더할 낱말: 이 소제목과 이어진 요청 표 행의 구분 · 기관(회차 4 S6 질의에 송전선 · 한국전력공사가 없어
    송전시설 조항이 5위 안에 없었다)."""
    rows: list[dict[str, Any]] = []
    if iid in REQUEST_ITEMS:
        rows = request_rows(by_id.get(iid))
    elif (iid, title) == ("S2", "시설 보호"):
        rows = [r for r in request_rows(by_id.get("S6")) if r.get("item") == "S2"]
    elif (iid, title) == ("S3", "동원 범위"):
        rows = [r for r in request_rows(by_id.get("S6")) if r.get("item") == "S3"]
    elif (iid, title) in (("E4", "관련 통제"), ("E3", "이송 요청")):
        rows = [r for r in request_rows(by_id.get("E6")) if r.get("item") == iid]
    return _agencies(rows)


def parts_of(
    item: dict[str, Any], by_id: dict[str, dict[str, Any]], common: dict[str, Any], scope: str = ""
) -> list[Part]:
    """이 항목에서 LLM에 줄 소제목(코드가 먼저 뺀 것은 없음). scope = 소제목 없는 항목의 「다루는 것」."""
    iid = item["id"]
    specs = SPECS.get(iid)
    if not specs:
        return [
            Part(
                UNTITLED,
                scope,
                _detail(iid, UNTITLED, item, by_id, common),
                _query_words(iid, UNTITLED, by_id),
            )
        ]
    have = section_numbers(item)
    out = []
    for sp in specs:
        if sp.section is not None and have and sp.section not in have:
            continue  # 규칙 엔진이 이 섹션을 만들지 않음 = 대상 없음
        out.append(
            Part(
                sp.title,
                sp.scope,
                _detail(iid, sp.title, item, by_id, common),
                _query_words(iid, sp.title, by_id),
            )
        )
    return out


def related_lines(iid: str, by_id: dict[str, dict[str, Any]]) -> list[str]:
    """[관련 항목의 일]: 요청 표 「항목」 칸으로 이어진 일.
    - 보통 항목: S6 · E6 표에서 이 항목 행 → 「S6 유관기관 동원·협조 요청: 한국전력공사 — 154 kV 송전선 …, 5시간 안」
    - S6 · E6: 표의 행이 어느 항목의 대상인지 → 「S2 진화 우선지역·보호대상·시설 보호: 한국전력공사 — …」"""
    out = []

    def name(x: str) -> str:
        return f"{x} {(by_id.get(x) or {}).get('name', '')}".strip()

    for src in REQUEST_ITEMS:
        for r in request_rows(by_id.get(src)):
            when = f", {r['when']}" if r.get("when") else ""
            body = f"{r.get('agency', '')} — {r.get('target', '')}{when}"
            if iid == src and r.get("item") and r["item"] != src:
                out.append(f"- {name(r['item'])}: {body}")
            elif r.get("item") == iid and iid != src:
                out.append(f"- {name(src)}: {body}")
    return out


_CODE = re.compile(r"(?<![A-Za-z0-9])([SE][1-6])(?![0-9])")


def codes_of(item: dict[str, Any], related: list[str]) -> set[str]:
    """문장에 쓸 수 있는 다른 항목 번호: [관련 항목의 일] 줄 머리의 번호 + 판정 한 줄(「단계 발령 협의(산림청장)는 S1」) +
    규칙 엔진 action의 see(S2 · S3 → S6, E3 · E4 · E5 → E6). 자기 번호는 뺀다."""
    out = {m.group(1) for ln in related if (m := re.match(r"^-\s*([SE][1-6])\b", ln))}
    out |= set(_CODE.findall(str(item.get("finding") or "")))
    out |= {
        str(b["see"])
        for s in item.get("sections", [])
        for b in s.get("blocks", [])
        if b.get("type") == "action" and b.get("see")
    }
    out.discard(item.get("id"))
    return out


def detail_lines(block: dict[str, Any]) -> list[str]:
    """자세히 보기 블록 → LLM이 읽을 글줄."""
    lines = judgment.block_lines(block)
    if block.get("caption"):
        lines = [f"({block['caption']})"] + lines
    return lines
