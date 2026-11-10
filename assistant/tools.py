# ruff: noqa: E501
"""어시스턴트 도구: 질문에 답할 재료를 꺼낸다. LLM은 도구 이름 · 인자만 고르고(plan.py), 값은 여기서 읽는다.

도구는 두 가지를 돌려준다.
- 상황 값(Fact): 규칙 엔진 묶음(proposals.context) · 지금 상황(incidents.situation) · 제안서 항목에서 읽은 값.
  `ref`는 자료 경로(답변의 data_refs)다. 예: context.area.slices[p=5].ha · situation.deployed.helicopter.total
- 근거 문서(chunk): 제안서 항목이 인용한 원문, 규칙 엔진 판정 근거, 근거 검색(RAG) 결과.

도구는 값을 계산하거나 판정하지 않는다(판정은 규칙 엔진). 없는 값은 「모름」 Fact로 이유와 함께 넘긴다
— 그래야 LLM이 「~ 정보가 없어」라고 근거 있게 답한다(근거 부족 규칙, 11/6).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent import cite
from assistant.context import (
    DEPLOYED_LABELS,
    GROUP_NAMES,
    GROUP_UNITS,
    ITEM_NAMES,
    Context,
    paragraphs,
    slot_value,
)


@dataclass(frozen=True)
class Fact:
    ref: str  # 자료 경로(data_ref)
    label: str
    value: str
    text: str = ""  # 규칙 모드(LLM 없음) 문장. 비면 「label은 value입니다.」

    def sentence(self) -> str:
        if self.text:
            return self.text
        return f"{josa(self.label, '은', '는')} {self.value}입니다."


@dataclass
class ToolResult:
    name: str
    title: str
    facts: list[Fact] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""  # 도구가 할 말(예: 예측 전이라 값 없음)


# ── 표기 ───────────────────────────────────────────────────────────

_DIGIT_BATCHIM = set("013678")


def _batchim(word: str) -> bool:
    w = re.sub(r"\([^()]*\)$", "", word.rstrip()).rstrip(" .")
    if w.endswith(("ha", "m/s", "km", "%")):
        return False
    ch = w[-1:] if w else ""
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    return ch in _DIGIT_BATCHIM


def josa(word: str, with_b: str, without_b: str) -> str:
    return word + (with_b if _batchim(word) else without_b)


def num(x: Any) -> str:
    """48.5 → 48.5, 723.0 → 723, 1234 → 1,234."""
    if x is None:
        return "알 수 없음"
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return f"{x:,}" if isinstance(x, int) else str(x)


def stage_name(s: str | None) -> str:
    if not s:
        return "알 수 없음"
    s = s.replace(" ", "")
    if s in ("초기대응", "INITIAL"):
        return "초기대응"
    m = re.search(r"([12])", s)
    return f"확산대응 {m.group(1)}단계" if m else s


ZONE = {
    "burning": "이미 닿음",
    "p5": "5시간 안 도달(위험구역)",
    "p8": "5~8시간 안 도달(잠재 위험구역)",
}


def reach(x: dict[str, Any]) -> str:
    """도달 시각 표기: 지금(이미 닿음) · 15:50~16:50(밤)."""
    if x.get("until") == "지금" or x.get("zone") == "burning":
        out = "지금(이미 닿음)"
    elif x.get("from"):
        out = f"{x['from']}~{x.get('until')}"
    else:
        out = f"{x.get('until')}까지"
    if x.get("night"):
        out += "(일몰 뒤)"
    if x.get("passed"):
        out += "(예상 시각 지남)"
    return out


def field_text(f: dict[str, Any]) -> str:
    parts = []
    if f.get("evacuated"):
        parts.append("대피 완료")
    if f.get("evac_order"):
        parts.append("대피명령 내림")
    if f.get("pre_evac_done"):
        parts.append("사전대피 완료")
    if f.get("alert_at"):
        parts.append(f"대피 문자 {f['alert_at']} 발송")
    return " · ".join(parts) if parts else "대피 보고 없음"


def plain(text: str) -> str:
    return (text or "").replace("**", "").strip()


def unknowns(ref: str, entries: list[dict[str, Any]] | None) -> list[Fact]:
    """엔진 묶음의 unknown · withheld(빠진 정보와 이유) → 「모름」 Fact."""
    out = []
    for i, u in enumerate(entries or []):
        what, why = u.get("field", ""), u.get("reason", "")
        if not what:
            continue
        # 엔진 이유가 개발 말투라 답에 그대로 나왔다(11-10 평가: 「MVP에서 제외되어」 「배정은 하지 않음(후보만 보여 줌)입니다」)
        why = re.sub(r"MVP\s?(?:에서)?\s?제외", "이 시스템이 판단에 쓰지 않음", why)
        why = why.replace("하지 않음(후보만 보여 줌)", "시스템은 후보만 보여 주고 정하지 않음")
        text = f"{what} 정보는 없습니다({why})." if why else f"{what} 정보는 없습니다."
        out.append(Fact(f"{ref}[{i}]", f"{what}(빠진 정보)", why or "자료 없음", text))
    return out


def _b(ctx: Context, key: str) -> dict[str, Any]:
    return ctx.bundles.get(key) or {}


NO_PROPOSAL = Fact(
    "proposal",
    "확산 예측 · 제안서",
    "아직 없음(예측을 먼저 실행해야 함)",
    "아직 확산 예측과 제안서가 없어 이 값은 알 수 없습니다. 확산 예측을 실행하면 확인할 수 있습니다.",
)


def _no_proposal(name: str, title: str) -> ToolResult:
    return ToolResult(name, title, facts=[NO_PROPOSAL])


# ── 도구 ───────────────────────────────────────────────────────────


class ToolBox:
    """도구 모음. evidence = 근거 서버 클라이언트(agent.clients.Evidence), 없으면 검색 도구는 빈 결과."""

    def __init__(self, ctx: Context, evidence: Any | None = None, k: int = 4):
        self.ctx, self.evidence, self.k = ctx, evidence, k

    def run(self, name: str, arg: str = "") -> ToolResult:
        spec = TOOLS.get(name)
        if spec is None:
            return ToolResult(name, name, note=f"모르는 도구: {name}")
        return spec.fn(self, (arg or "").strip())

    # 근거 원문: 제안서가 인용한 것 → 없으면 근거 서버에서
    def basis_chunks(self, ids: list[str], origin: str = "판정 근거") -> list[dict[str, Any]]:
        out, missing = [], []
        for i in dict.fromkeys(ids or []):
            found = [
                c
                for it in self.ctx.items
                for c in it.get("evidence") or []
                if c.get("chunk_id") == i or c.get("chunk_id", "").startswith(i + "-")
            ]
            if found:
                out += found
            else:
                missing.append(i)
        if missing and self.evidence is not None:
            from agent.retrieve import expand_pages

            try:
                got, _ = self.evidence.fetch(expand_pages(missing))
            except Exception:  # noqa: BLE001 — 근거 서버가 없으면 인용 원문만으로
                got = []
            for c in got:
                out.append({**c, "origin": origin, "label": cite.label(c)})
        uniq: dict[str, dict[str, Any]] = {}
        for c in out:
            uniq.setdefault(c["chunk_id"], c)
        return list(uniq.values())

    # 1. 개요
    def overview(self, arg: str) -> ToolResult:
        ctx, r = self.ctx, ToolResult("overview", "산불 개요")
        inc = ctx.incident
        r.facts += [
            Fact("incident.name", "산불", inc.get("name") or "이름 없음"),
            Fact("incident.status", "진행 상태", inc.get("status") or "알 수 없음"),
        ]
        if inc.get("started_at"):
            r.facts.append(Fact("incident.started_at", "발생 시각", inc["started_at"]))
        if inc.get("reported_at"):
            r.facts.append(Fact("incident.reported_at", "신고 접수 시각", inc["reported_at"]))
        if inc.get("address"):
            r.facts.append(Fact("incident.address", "발생 장소", inc["address"]))
        r.facts.append(Fact("reader", "통합지휘권자(읽는 사람)", ctx.reader))
        if not ctx.has_proposal:
            r.facts.append(NO_PROPOSAL)
            if inc.get("current_stage"):
                r.facts.append(
                    Fact(
                        "incident.current_stage", "현재 대응단계", stage_name(inc["current_stage"])
                    )
                )
            return r
        p, t = ctx.proposal or {}, _b(ctx, "time")
        st, cm, area = _b(ctx, "stage"), _b(ctx, "commander"), _b(ctx, "area")
        r.facts += [
            Fact("proposal.version", "보고 있는 제안서 버전", p.get("version") or "알 수 없음"),
            Fact(
                "context.time.base_at",
                "제안서 기준 시각(현재 화선 관측)",
                t.get("base_at") or "알 수 없음",
            ),
            Fact("proposal.trigger", "제안서를 만든 계기", p.get("trigger") or "알 수 없음"),
            Fact(
                "context.stage.now.stage",
                "현재 기준 대응단계",
                stage_name((st.get("now") or {}).get("stage")),
            ),
            Fact(
                "context.stage.p5.stage",
                "5시간 뒤 예측 기준 대응단계",
                stage_name((st.get("p5") or {}).get("stage")),
            ),
            Fact(
                "context.commander.now.title",
                "현재 통합지휘권자",
                (cm.get("now") or {}).get("title", "알 수 없음"),
            ),
            Fact("context.area.now_ha", "현재 화선 면적", f"{num(area.get('now_ha'))} ha"),
        ]
        s5 = next((s for s in area.get("slices", []) if s.get("p") == 5), None)
        if s5:
            r.facts.append(
                Fact(
                    "context.area.slices[p=5].ha",
                    f"5시간 뒤({s5['until']}) 예상 면적",
                    f"{num(s5['ha'])} ha",
                )
            )
        return r

    # 2. 대응단계 · 지휘권
    def stage(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            r = _no_proposal("stage", "대응단계 판정")
            if ctx.incident.get("current_stage"):
                r.facts.append(
                    Fact(
                        "incident.current_stage",
                        "현재 대응단계",
                        stage_name(ctx.incident["current_stage"]),
                    )
                )
            return r
        r = ToolResult("stage", "대응단계 판정 · 지휘권")
        st = _b(ctx, "stage")
        names = {"area": "피해면적", "wind": "평균풍속", "facility": "시설피해 우려"}
        for when, head in (("now", "현재 기준"), ("p5", "5시간 뒤(P5) 예측 기준")):
            s = st.get(when) or {}
            if not s:
                continue
            ref = f"context.stage.{when}"
            r.facts.append(Fact(f"{ref}.stage", f"{head} 대응단계", stage_name(s.get("stage"))))
            minimum = "최소 " if s.get("facility_is_minimum") else ""
            vals = {
                "area": f"{num(s.get('area_ha'))} ha",
                "wind": f"{num(s.get('wind_ms'))} m/s",
                "facility": f"{minimum}{num(s.get('facility'))}동",
            }
            for k, label in names.items():
                r.facts.append(
                    Fact(
                        f"{ref}.{k}",
                        f"{head} {label}",
                        f"{vals[k]} → {stage_name(s.get('by_' + k))} 기준",
                    )
                )
            decided = [names.get(d, d) for d in s.get("decided_by") or []]
            if decided:
                r.facts.append(
                    Fact(f"{ref}.decided_by", f"{head} 단계를 정한 요소", " · ".join(decided))
                )
        ch = st.get("change") or {}
        if ch:
            kinds = {"new": "첫 판정", "up": "격상", "down": "하향", "same": "유지"}
            r.facts.append(
                Fact(
                    "context.stage.change",
                    "직전 제안서 대비 현재 단계 변화",
                    f"{stage_name(ch.get('prev')) if ch.get('prev') else '직전 없음'} → {stage_name(ch.get('now'))} ({kinds.get(ch.get('type'), ch.get('type'))})",
                )
            )
        cm = _b(ctx, "commander")
        if cm:
            r.facts.append(
                Fact(
                    "context.commander.now.title",
                    "현재 통합지휘권자",
                    (cm.get("now") or {}).get("title", ""),
                )
            )
            nxt = cm.get("next") or {}
            if nxt.get("title"):
                reasons = " · ".join(
                    x.get("text", "") for x in nxt.get("reasons") or [] if x.get("text")
                )
                r.facts.append(
                    Fact(
                        "context.commander.next",
                        "단계가 오르면 넘겨받을 통합지휘권자",
                        nxt["title"] + (f"(사유: {reasons})" if reasons else ""),
                    )
                )
        r.facts += unknowns("context.stage.unknown", st.get("unknown"))
        r.chunks = self.basis_chunks((st.get("basis") or []) + (cm.get("basis") or []))
        return r

    # 3. 확산
    def spread(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            return _no_proposal("spread", "확산 예측")
        r = ToolResult("spread", "확산 예측(P1~P8)")
        area = _b(ctx, "area")
        r.facts.append(
            Fact("context.area.now_ha", "현재 화선 면적", f"{num(area.get('now_ha'))} ha")
        )
        for s in area.get("slices", []):
            r.facts.append(
                Fact(
                    f"context.area.slices[p={s['p']}].ha",
                    f"{s['p']}시간 뒤({s['until']}까지) 예상 면적",
                    f"{num(s['ha'])} ha",
                )
            )
        for f in area.get("first_over", []):
            r.facts.append(
                Fact(
                    f"context.area.first_over[ha={f['ha']}]",
                    f"면적이 처음 {num(f['ha'])} ha를 넘는 시점",
                    f"{f['p']}시간 뒤({f['until']}까지, {num(f.get('area_ha'))} ha)",
                )
            )
        sp = _b(ctx, "spread")
        if sp.get("dir"):
            r.facts.append(
                Fact(
                    "context.spread.dir",
                    "주 확산 방향(현재 화선 중심 → 5시간 뒤 중심)",
                    f"{sp['dir']}쪽 {num(sp.get('km'))} km",
                )
            )
        for v in sp.get("next_villages", []):
            r.facts.append(
                Fact(
                    f"context.spread.next_villages[{v['name']}]",
                    f"다음에 닿을 마을 {v.get('emd', '')} {v['name']}",
                    reach(v),
                )
            )
        r.facts += unknowns("context.area.unknown", area.get("unknown"))
        return r

    # 4. 기상
    def weather(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            return _no_proposal("weather", "기상")
        r = ToolResult("weather", "기상(예측에 넣은 값)")
        w = _b(ctx, "weather")
        now = w.get("now") or {}
        if now:
            r.facts.append(
                Fact(
                    "context.weather.now",
                    "현재 기상",
                    f"{now.get('dir', '')}풍 {num(now.get('ms'))} m/s · 기온 {num(now.get('temp_c'))}℃ · 습도 {num(now.get('rh_pct'))}%",
                )
            )
        if w.get("mean_0_5_ms") is not None:
            r.facts.append(
                Fact(
                    "context.weather.mean_0_5_ms",
                    "0~5시간 평균 풍속",
                    f"{num(w['mean_0_5_ms'])} m/s",
                )
            )
        mx = w.get("max") or {}
        if mx:
            r.facts.append(
                Fact(
                    "context.weather.max",
                    "8시간 중 가장 센 바람",
                    f"{mx.get('dir', '')}풍 {num(mx.get('ms'))} m/s({', '.join(mx.get('hours') or [])})",
                )
            )
        for h in w.get("hourly", []):
            r.facts.append(
                Fact(
                    f"context.weather.hourly[h={h['h']}]",
                    f"{h['at']} 바람",
                    f"{h.get('dir', '')}풍 {num(h.get('ms'))} m/s",
                )
            )
        sun = _b(ctx, "time").get("sun") or {}
        if (sun.get("sunset") or {}).get("at"):
            r.facts.append(Fact("context.time.sun.sunset", "일몰", sun["sunset"]["at"]))
        if (sun.get("sunrise") or {}).get("at"):
            r.facts.append(Fact("context.time.sun.sunrise", "일출", sun["sunrise"]["at"]))
        r.facts += unknowns("context.weather.unknown", w.get("unknown"))
        return r

    # 5. 진화자원
    def resources(self, arg: str) -> ToolResult:
        ctx = self.ctx
        r = ToolResult("resources", "진화자원 투입 · 동원 범위")
        dep = ctx.situation.get("deployed") or {}
        for group, labels in DEPLOYED_LABELS.items():
            g = dep.get(group)
            if not g:
                continue
            # 합계만 보고된 남는 몫도 미상 · 기타로 보인다(엔진 입력과 같은 규칙)
            by = " · ".join(
                f"{labels[k]} {num(v)}" for k in labels if (v := slot_value(g, group, k))
            )
            unit = GROUP_UNITS[group]
            r.facts.append(
                Fact(
                    f"situation.deployed.{group}.total",
                    f"투입된 {GROUP_NAMES[group]}(합계)",
                    f"{num(g.get('total', 0))}{unit}" + (f"({by})" if by else ""),
                )
            )
        if not dep:
            r.facts.append(
                Fact(
                    "situation.deployed",
                    "투입 진화자원",
                    "보고 없음",
                    "투입된 진화자원 수는 아직 보고되지 않았습니다.",
                )
            )
        res = _b(ctx, "resources")
        if res.get("reported_at"):
            r.facts.append(
                Fact(
                    "context.resources.reported_at",
                    "제안서에 쓴 투입 수의 보고 시각",
                    res["reported_at"],
                )
            )
        orgs = (res.get("crew") or {}).get("orgs") or []
        if orgs:
            r.facts.append(Fact("context.resources.crew.orgs", "인력 투입 기관", " · ".join(orgs)))
        mob = _b(ctx, "mobilization")
        for when, head in (("now", "현재 단계"), ("p5", "5시간 뒤 예측 단계")):
            m = mob.get(when) or {}
            if not m:
                continue
            ground = m.get("ground") or {}
            home, extra = ground.get("home") or {}, ground.get("extra") or {}
            text = f"{home.get('name', '')} {home.get('share', '')}"
            if extra.get("names"):
                text += f" + {' · '.join(extra['names'])} {extra.get('share', '')}"
            air = m.get("air") or {}
            air_text = (
                "전국"
                if air.get("kind") == "national"
                else (f"반경 {num(air.get('km'))} km" if air.get("km") else "")
            )
            r.facts.append(
                Fact(
                    f"context.mobilization.{when}",
                    f"{head}({stage_name(m.get('stage'))}) 동원 범위",
                    f"지상 {text.strip()}" + (f" · 공중(헬기) {air_text}" if air_text else ""),
                )
            )
        for b in (mob.get("bases") or [])[:8]:
            avail = (b.get("available") or {}).get("helicopters")
            r.facts.append(
                Fact(
                    f"context.mobilization.bases[{b['name']}]",
                    f"진화자원 기지 {b['name']}",
                    f"{b.get('kind', '')} · {b.get('jurisdiction', '')} · {num(b.get('km'))} km"
                    + (f" · 대기 헬기 {avail}대" if avail is not None else ""),
                )
            )
        r.facts += unknowns("context.mobilization.unknown", mob.get("unknown"))
        r.facts += unknowns("context.resources.unknown", res.get("unknown"))
        if mob.get("basis"):
            r.chunks = self.basis_chunks(mob["basis"])
        return r

    # 6. 마을 목록
    def villages(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            return _no_proposal("villages", "위험구역 마을")
        r = ToolResult("villages", "위험구역 · 잠재 위험구역 마을")
        vs = ctx.zone_villages()
        if not vs:
            r.facts.append(
                Fact(
                    "context.villages",
                    "8시간 안에 닿는 마을",
                    "없음",
                    "8시간 예측 범위 안에 닿는 마을은 없습니다.",
                )
            )
        for v in vs:
            pop = f"인구 {num(v.get('population'))}명"
            if v.get("pop65_est") is not None:
                pop += f"(65세 이상 약 {num(v['pop65_est'])}명)"
            r.facts.append(
                Fact(
                    f"context.villages.{v['zone']}[{v['id']}]",
                    f"{v.get('emd', '')} {v['name']}",
                    f"{ZONE.get(v['zone'], v['zone'])} · {reach(v)} · {pop} · {field_text(ctx.village_field(v['id']))}",
                )
            )
        # 11-10 평가: 「다음은 어디를 대피시켜야 해?」에 이미 불이 닿았고 대피 보고 없는 석탑리 · 신안리를 건너뛰고
        # 5~8시간 마을을 댐 → 대피명령 · 대피 보고가 없는 마을을 불이 닿는 순서로 시스템이 줄 세워 준다
        order = {"burning": 0, "p5": 1, "p8": 2}
        handled = ("evacuated", "evac_order", "pre_evac_done")
        waiting = sorted(
            (v for v in vs if not any(ctx.village_field(v["id"]).get(k) for k in handled)),
            key=lambda v: (order.get(v["zone"], 3), v.get("from") or v.get("until") or ""),
        )
        if vs:
            names = " → ".join(f"{v.get('emd', '')} {v['name']}({reach(v)})" for v in waiting)
            r.facts.append(
                Fact(
                    "context.villages.waiting",
                    "대피명령 · 대피 완료 보고가 아직 없는 마을(불이 닿는 순서)",
                    names or "없음(모든 마을에 대피명령 또는 대피 완료 보고가 있음)",
                )
            )
        tot = _b(ctx, "villages").get("totals") or {}
        for k, head in (("burning", "이미 닿은"), ("p5", "5시간 안"), ("p8", "5~8시간 안")):
            t = tot.get(k)
            if t and t.get("count"):
                r.facts.append(
                    Fact(
                        f"context.villages.totals.{k}",
                        f"{head} 마을 수 · 인구",
                        f"{t['count']}곳 · {num(t.get('population'))}명",
                    )
                )
        r.facts += unknowns("context.villages.unknown", _b(ctx, "villages").get("unknown"))
        return r

    # 7. 마을 하나
    def find_villages(self, name: str) -> list[dict[str, Any]]:
        """이름(「괴산리」 · 「괴산」 · 「안평면 괴산리」)으로 엔진 마을 → 마을 목록 순으로 찾는다."""
        name = re.sub(r"\s+", " ", name or "").strip()
        emd = None
        m = re.match(r"([가-힣]+[읍면동])\s+([가-힣]+)", name)
        if m:
            emd, name = m.group(1), m.group(2)
        cands = [name, name + "리"] if not name.endswith("리") else [name]
        pool = self.ctx.zone_villages() + [
            v
            for v in self.ctx.villages
            if str(v.get("id")) not in {str(z["id"]) for z in self.ctx.zone_villages()}
        ]
        hits = [v for v in pool if v.get("name") in cands and (emd is None or v.get("emd") == emd)]
        uniq: dict[str, dict[str, Any]] = {}
        for v in hits:
            uniq.setdefault(str(v["id"]), v)
        return list(uniq.values())

    def village(self, arg: str) -> ToolResult:
        ctx = self.ctx
        r = ToolResult("village", f"마을 {arg}")
        hits = self.find_villages(arg)
        if not hits:
            name = arg if arg.endswith("리") else arg + "리"
            if (
                ctx.has_proposal
            ):  # 범위 밖이라는 것도 근거 있는 답이다(11-09 평가: 14:20 회차의 박곡리)
                r.facts.append(
                    Fact(
                        f"context.villages[{name}]",
                        f"{name} 도달 구분",
                        "이번 제안서의 위험구역 · 잠재 위험구역(8시간 예측 범위) 마을 목록에 없음",
                        f"{name}은 이번 제안서의 위험구역 · 잠재 위험구역(8시간 예측 범위) 마을 목록에 없습니다.",
                    )
                )
            else:
                r.note = f"「{arg}」 마을을 찾지 못했습니다."
            return r
        zone_ids = {str(z["id"]) for z in ctx.zone_villages()}
        shelters = {s.get("village"): s for s in _b(ctx, "shelters").get("villages", [])}
        for v in hits:
            vid, label = str(v["id"]), f"{v.get('emd', '')} {v['name']}"
            if vid in zone_ids:
                z = next(x for x in ctx.zone_villages() if str(x["id"]) == vid)
                r.facts.append(
                    Fact(
                        f"context.villages.{z['zone']}[{vid}].zone",
                        f"{label} 도달 구분",
                        ZONE.get(z["zone"], z["zone"]),
                    )
                )
                r.facts.append(
                    Fact(
                        f"context.villages.{z['zone']}[{vid}].reach",
                        f"{label} 불이 닿는 예상 시각",
                        reach(z),
                    )
                )
                r.facts.append(
                    Fact(
                        f"context.villages.{z['zone']}[{vid}].population",
                        f"{label} 인구",
                        f"{num(z.get('population'))}명",
                    )
                )
                if z.get("pop65_est") is not None:
                    r.facts.append(
                        Fact(
                            f"context.villages.{z['zone']}[{vid}].pop65_est",
                            f"{label} 65세 이상(추정)",
                            f"{num(z['pop65_est'])}명",
                        )
                    )
                sh = shelters.get(v["name"])
                if sh:
                    for kind, head in (
                        ("village_sites", "마을 대피소 후보"),
                        ("hub_sites", "거점 대피소 후보"),
                    ):
                        for s in sh.get(kind) or []:
                            cap = (
                                f" · 수용 {num(s['capacity'])}명"
                                if s.get("capacity") is not None
                                else ""
                            )
                            r.facts.append(
                                Fact(
                                    f"context.shelters.villages[{v['name']}].{kind}[{s['name']}]",
                                    f"{label} {head}",
                                    f"{s['name']}({s.get('kind', '')}, {num(s.get('km'))} km{cap})",
                                )
                            )
            elif ctx.has_proposal:
                r.facts.append(
                    Fact(
                        f"villages[{vid}].zone",
                        f"{label} 도달 구분",
                        "8시간 예측 범위 밖",
                        f"{label}은(는) 8시간 예측 범위 밖이라 닿는 시각이 없습니다.",
                    )
                )
            r.facts.append(
                Fact(
                    f"situation.villages[{vid}]",
                    f"{label} 지금 대피 상황",
                    field_text(ctx.village_field(vid)),
                )
            )
        for a in _b(ctx, "alerts").get("log", []):
            if a.get("village") in {h["name"] for h in hits}:
                r.facts.append(
                    Fact(
                        f"context.alerts.log[{a['village']}]",
                        f"{a.get('emd', '')} {a['village']} {a.get('kind', '문자')}",
                        f"{a.get('at')} 발송",
                    )
                )
        return r

    # 8. 대피소
    def shelters(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            return _no_proposal("shelters", "대피소 후보")
        r = ToolResult("shelters", "대피소 후보(마을 대표점에서 직선거리)")
        want = {h["name"] for h in self.find_villages(arg)} if arg else None
        sh = _b(ctx, "shelters")
        for v in sh.get("villages", []):
            if want is not None and v.get("village") not in want:
                continue
            label = f"{v.get('emd', '')} {v['village']}"
            sites = [
                f"{s['name']}({num(s.get('km'))} km"
                + (f", 수용 {num(s['capacity'])}명" if s.get("capacity") is not None else "")
                + ")"
                for k in ("village_sites", "hub_sites")
                for s in v.get(k) or []
            ]
            r.facts.append(
                Fact(
                    f"context.shelters.villages[{v['village']}]",
                    f"{label} 대피소 후보",
                    " · ".join(sites) if sites else "후보 없음",
                )
            )
            for e in v.get("excluded") or []:
                r.facts.append(
                    Fact(
                        f"context.shelters.villages[{v['village']}].excluded[{e.get('name')}]",
                        f"{label} 후보에서 뺀 곳",
                        f"{e.get('name')}({e.get('reason')})",
                    )
                )
        r.facts += unknowns("context.shelters.unknown", sh.get("unknown"))
        if not r.facts:
            r.note = "해당 마을의 대피소 후보가 제안서에 없습니다."
        r.chunks = self.basis_chunks(sh.get("basis") or [])
        return r

    # 9. 취약시설 · 보호 시설 · 주택
    def facilities(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            return _no_proposal("facilities", "시설")
        r = ToolResult("facilities", "취약시설 · 보호 시설 · 주택")
        vul = _b(ctx, "vulnerable")
        for f in vul.get("in_range", []):
            r.facts.append(
                Fact(
                    f"context.vulnerable.in_range[{f['id']}]",
                    f"취약시설 {f['name']}({f.get('kind', '')})",
                    f"{f.get('emd', '')} {f.get('village', '')} · {reach(f)}"
                    + (" · 낮에만 운영" if f.get("daytime_only") else ""),
                )
            )
        if not vul.get("in_range"):
            r.facts.append(
                Fact(
                    "context.vulnerable.in_range",
                    "8시간 안 취약시설",
                    # 11-10 평가 5차: 값이 「없음」뿐이라 「취약시설은 정보가 없습니다」로 잘못 읽음
                    "범위 안에 든 곳 없음(요양원 · 병원 · 학교 등 0곳)",
                    "8시간 예측 범위 안에 취약시설은 없습니다.",
                )
            )
        pr = _b(ctx, "protected")
        for p in pr.get("power_lines", []):
            r.facts.append(
                Fact(
                    f"context.protected.power_lines[{p['id']}]",
                    p["name"],
                    f"{reach(p)} · 5시간 범위 안 {num(p.get('km_p5'))} km",
                )
            )
        for kind, head in (("substations", "변전 · 발전 시설"), ("temples", "사찰")):
            for s in pr.get(kind, []):
                r.facts.append(
                    Fact(
                        f"context.protected.{kind}[{s.get('name')}]",
                        f"{head} {s.get('name')}",
                        reach(s),
                    )
                )
        her = pr.get("heritage") or {}
        if her:
            r.facts.append(
                Fact(
                    "context.protected.heritage.in_p8",
                    "8시간 범위 안 국가유산",
                    f"{num(her.get('in_p8', 0))}곳(시군 전체 {num(her.get('total'))}곳 중)",
                )
            )
            for h in her.get("items", []):
                r.facts.append(
                    Fact(
                        f"context.protected.heritage.items[{h.get('name')}]",
                        f"국가유산 {h.get('name')}",
                        f"{h.get('grade', '')} · {reach(h)}",
                    )
                )
        hs = _b(ctx, "houses").get("within_p5") or {}
        if hs:
            mini = "최소 " if _b(ctx, "houses").get("is_minimum") else ""
            by = " · ".join(f"{x['village']} {x['houses']}동" for x in hs.get("by_village", []))
            r.facts.append(
                Fact(
                    "context.houses.within_p5.total",
                    "5시간 범위 안 주택",
                    f"{mini}{num(hs.get('total'))}동" + (f"({by})" if by else ""),
                )
            )
        r.facts += unknowns("context.vulnerable.unknown", vul.get("unknown"))
        r.facts += unknowns("context.protected.unknown", pr.get("unknown"))
        return r

    # 10. 도로
    def roads(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            return _no_proposal("roads", "도로")
        r = ToolResult("roads", "통제가 필요할 수 있는 도로 구간(5시간 범위)")
        rd = _b(ctx, "roads")
        for i, s in enumerate(rd.get("sections", [])):
            name = s.get("ref") or s.get("road") or "도로"
            police = (s.get("police") or {}).get("name")
            km = s.get("km")
            inside = ""  # 11-10 평가: 「범위 안 0 km」 → 0.1 km 미만은 가장자리에 걸친 것
            if isinstance(km, int | float):
                inside = (
                    f" · 범위 안 {num(km)} km"
                    if km >= 0.1
                    else " · 범위 가장자리에 걸침(0.1 km 미만)"
                )
            r.facts.append(
                Fact(
                    f"context.roads.sections[{i}]",
                    f"{name} {s.get('section', '')} 구간",
                    f"{reach(s)}{inside}" + (f" · 가까운 경찰서 {police}" if police else ""),
                )
            )
        if not rd.get("sections"):
            r.facts.append(
                Fact(
                    "context.roads.sections",
                    "5시간 범위에 걸친 큰 도로",
                    "없음",
                    "5시간 예측 범위에 걸친 큰 도로는 없습니다.",
                )
            )
        r.facts += unknowns("context.roads.unknown", rd.get("unknown"))
        return r

    # 11. 재난문자 · 방송
    def alerts(self, arg: str) -> ToolResult:
        ctx = self.ctx
        r = ToolResult("alerts", "재난문자 · 방송 전파")
        if "disaster_sms_sent" in ctx.situation:
            r.facts.append(
                Fact(
                    "situation.disaster_sms_sent",
                    "재난문자 송출(지금 상황)",
                    "송출함" if ctx.situation["disaster_sms_sent"] else "송출 보고 없음",
                )
            )
        if not ctx.has_proposal:
            r.note = "예측 전이라 전파 대상 읍면 제안은 없습니다."
            return r
        al = _b(ctx, "alerts")
        # 11-10 평가: 「대피 준비(Set) 단계이므로 재난문자 발송 대상이 아닙니다」 → Set도 문자 · 방송 대상임을 값에 밝힌다
        lv = {
            "go": "대피(Go) 단계 — 대피하라고 알림",
            "set": "대피 준비(Set) 단계 — 대피를 준비하라고 알림",
        }
        ch = {"new": "새로 추가", "up": "상향", "same": "유지", "down": "하향"}
        for a in al.get("areas", []):
            names = ", ".join(a.get("villages") or [])
            r.facts.append(
                Fact(
                    f"context.alerts.areas[{a['emd']}]",
                    f"{a.get('sigungu', '')} {a['emd']} 재난문자 · 방송 대상",
                    f"{lv.get(a.get('level'), a.get('level'))}, 대상 마을: {names}"
                    f" (앞 제안서와 비교: {ch.get(a.get('change'), a.get('change'))})",
                )
            )
        for a in al.get("removed", []):
            r.facts.append(
                Fact(
                    f"context.alerts.removed[{a.get('emd')}]", f"{a.get('emd')} 전파", "이번에 빠짐"
                )
            )
        for a in al.get("log", []):
            r.facts.append(
                Fact(
                    f"context.alerts.log[{a.get('village')}]",
                    f"{a.get('emd', '')} {a.get('village')} {a.get('kind', '문자')}",
                    f"{a.get('at')} 발송",
                )
            )
        r.chunks = self.basis_chunks(al.get("basis") or [])
        return r

    # 12. 관서
    def agencies(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            return _no_proposal("agencies", "관계기관")
        r = ToolResult("agencies", "관할 소방 · 경찰 · 응급의료기관")
        ag = _b(ctx, "agencies")
        for a in ag.get("by_sigungu", []):
            r.facts.append(
                Fact(
                    f"context.agencies.by_sigungu[{a['sigungu']}]",
                    f"{a['sigungu']} 관할 소방서 · 경찰서",
                    f"{a.get('fire')} · {a.get('police')}",
                )
            )
        for h in ag.get("emergency_medical", [])[:5]:
            r.facts.append(
                Fact(
                    f"context.agencies.emergency_medical[{h['name']}]",
                    f"응급의료기관 {h['name']}",
                    f"{h.get('sigungu', '')} · {num(h.get('km'))} km",
                )
            )
        r.facts += unknowns("context.agencies.unknown", ag.get("unknown"))
        return r

    # 13. 제안서 항목 하나
    def item(self, arg: str) -> ToolResult:
        ctx = self.ctx
        code = (arg or "").upper()
        it = ctx.item(code)
        if it is None:
            r = ToolResult("item", f"제안 항목 {code}")
            if code in ITEM_NAMES and ctx.has_proposal:
                r.facts.append(
                    Fact(
                        f"items.{code}",
                        f"{code} {ITEM_NAMES[code]}",
                        "이번 제안서에 없음",
                        f"{code} {ITEM_NAMES[code]} 항목은 이번 상황에서 발동하지 않아 제안서에 없습니다.",
                    )
                )
            else:
                r.note = f"{code} 항목이 제안서에 없습니다."
            return r
        name = it.get("name") or ITEM_NAMES.get(code, "")
        r = ToolResult("item", f"제안 항목 {code} {name}")
        d = it.get("detail") or {}
        states = {
            "ok": "제안 생성",
            "partial": "일부만 생성",
            "failed": "근거 있는 제안 없음",
            "reused": "직전 회차 제안 재사용",
        }
        if d.get("finding"):
            r.facts.append(
                Fact(
                    f"items.{code}.finding",
                    f"{code} 판정",
                    d["finding"],
                    f"{code} {name} 판정: {d['finding']}.",
                )
            )
        n = {
            "paragraph": 0,
            "list": 0,
            "table": 0,
            "note": 0,
        }  # 블록 종류마다 번호(경로가 겹치지 않게)
        for s in it.get("sections") or []:
            title = f" · {s['title']}" if s.get("title") else ""
            for b in s.get("blocks", []):
                kind = b.get("type")
                if kind == "paragraph":
                    body = plain(b.get("body") or b.get("text", ""))
                    r.facts.append(
                        Fact(
                            f"items.{code}.paragraphs[{n['paragraph']}]",
                            f"{code} 제안 문장({b.get('category', '')}, 근거 {b.get('cite', '')})",
                            body,
                            f"제안서 {code}({b.get('category', '')}): {body}",
                        )
                    )
                    n["paragraph"] += 1
                elif kind == "list":
                    for row in b.get("rows", []):
                        r.facts.append(
                            Fact(
                                f"items.{code}.list[{n['list']}]",
                                f"{code} 목록{title}",
                                plain(row.get("text", "")),
                            )
                        )
                        n["list"] += 1
                elif kind == "table":
                    for row in b.get("rows", []):
                        if isinstance(row, dict):
                            cells = " · ".join(
                                f"{k} {plain(str(v))}"
                                for k, v in row.items()
                                if v not in (None, "")
                            )
                            r.facts.append(
                                Fact(
                                    f"items.{code}.table[{n['table']}]",
                                    f"{code} 표 행{title}",
                                    cells,
                                )
                            )
                            n["table"] += 1
                elif kind == "note":
                    r.facts.append(
                        Fact(
                            f"items.{code}.note[{n['note']}]",
                            f"{code} 참고",
                            plain(b.get("text", "")),
                        )
                    )
                    n["note"] += 1
        if it.get("state") not in (
            None,
            "",
            "ok",
        ):  # 정상이면 알릴 것이 없다(11-10 평가: 「생성 상태는 제안 생성입니다」)
            r.facts.append(
                Fact(
                    f"items.{code}.state",
                    f"{code} {name} 생성 상태",
                    states.get(it.get("state"), it.get("state", "")),
                )
            )
        r.facts += unknowns(f"items.{code}.withheld", d.get("withheld"))
        r.chunks = list(it.get("evidence") or [])
        return r

    # 14. 제안서 전체
    def proposal(self, arg: str) -> ToolResult:
        ctx = self.ctx
        if not ctx.has_proposal:
            return _no_proposal("proposal", "제안서")
        r = ToolResult("proposal", f"제안서 {ctx.proposal.get('version', '')} 항목 요약")
        counts: dict[str, int] = {}
        for it in ctx.items:
            cats = it.get("categories") or []
            for c in cats:
                counts[c] = counts.get(c, 0) + 1
            n = len(paragraphs(it.get("sections") or []))
            r.facts.append(
                Fact(
                    f"items.{it['item_code']}",
                    f"{it['item_code']} {it.get('name') or ITEM_NAMES.get(it['item_code'], '')}",
                    (f"구분 {' · '.join(cats)} · 제안 {n}개" if cats else "근거 있는 제안 없음"),
                )
            )
        missing = [c for c in ITEM_NAMES if c not in ctx.item_codes()]
        if missing:
            r.facts.append(
                Fact(
                    "items",
                    "이번에 발동하지 않은 항목",
                    " · ".join(f"{c} {ITEM_NAMES[c]}" for c in missing),
                )
            )
        if counts:
            r.facts.append(
                Fact(
                    "items.categories",
                    "구분별 항목 수",
                    " · ".join(f"{k} {v}개" for k, v in counts.items()),
                )
            )
        return r

    # 15. 바뀐 것
    def changes(self, arg: str) -> ToolResult:
        ctx = self.ctx
        r = ToolResult("changes", "직전 제안서 대비 변경 · 최근 정정")
        prev = ctx.previous
        if ctx.has_proposal and prev:
            # 앱은 제안서 비교 API(PROP-04)와 같은 「변경」 판단을 넣어 준다(표현만 바뀐 것은 변경 아님).
            # 없으면(CLI 등) 문장 · 구분을 그대로 비교한다
            kinds = prev.get("changes") or self._text_changes(prev)
            ver = prev.get("version")
            for code in [c for c in ITEM_NAMES if c in kinds]:
                kind, name = kinds[code], ITEM_NAMES.get(code, "")
                if kind == "REMOVED":
                    r.facts.append(
                        Fact(
                            f"items.{code}",
                            f"{code} {name}",
                            f"이번 제안서에서 빠짐({ver}에는 있었음)",
                        )
                    )
                elif kind == "NEW":
                    r.facts.append(
                        Fact(
                            f"items.{code}",
                            f"{code} {name}",
                            f"이번 제안서에 새로 생김({ver}에는 없었음)",
                        )
                    )
                elif kind == "CHANGED":
                    r.facts.append(
                        Fact(f"items.{code}.text", f"{code} {name}", f"{ver} 대비 내용이 바뀜")
                    )
            if not r.facts:
                r.facts.append(
                    Fact(
                        "items",
                        f"{prev.get('version')} 대비 바뀐 항목",
                        "없음",
                        f"{prev.get('version')} 대비 바뀐 항목은 없습니다.",
                    )
                )
            r.facts.insert(
                0, Fact("previous.version", "비교한 직전 제안서", prev.get("version") or "")
            )
        elif ctx.has_proposal:
            r.note = "직전 제안서가 없어(첫 제안서) 비교할 것이 없습니다."
        if not r.facts and not r.note and not ctx.corrections:
            r.facts.append(
                Fact(
                    "corrections",
                    "비교할 직전 제안서 · 상황 정정",
                    "없음",
                    "아직 비교할 직전 제안서나 상황 정정이 없습니다.",
                )
            )
        for c in ctx.corrections[:5]:
            labels = " · ".join(ch.get("label") or change_label(ch) for ch in c.get("changes", []))
            r.facts.append(
                Fact(
                    f"corrections[{c.get('id')}]",
                    f"상황 정정({c.get('created_at', '')})",
                    f"「{c.get('input_text', '')}」 → {labels}",
                )
            )
        return r

    def _text_changes(self, prev: dict[str, Any]) -> dict[str, str]:
        """{코드: NEW · REMOVED · CHANGED} — 문장 · 구분이 다르면 CHANGED(앱 판단이 없을 때만)."""
        before = {i["item_code"]: i for i in prev.get("items", [])}
        now = {i["item_code"]: i for i in self.ctx.items}
        out = {}
        for code in [c for c in ITEM_NAMES if c in before or c in now]:
            a, b = before.get(code), now.get(code)
            if a is None or b is None:
                out[code] = "NEW" if a is None else "REMOVED"
            elif list(a.get("categories") or []) != list(b.get("categories") or []) or a.get(
                "text"
            ) != b.get("text"):
                out[code] = "CHANGED"
        return out

    # 16. 근거 검색
    def search(self, arg: str) -> ToolResult:
        r = ToolResult("search", f"근거 검색: {arg}")
        if not arg:
            r.note = "검색할 말이 없습니다."
            return r
        if self.evidence is None:
            r.note = "근거 검색 서버를 쓸 수 없습니다(제안서가 인용한 원문만 볼 수 있음)."
            return r
        hits = self.evidence.search(arg, k=self.k)
        for h in hits:
            r.chunks.append({**h, "origin": "검색", "label": cite.label(h)})
        if not hits:
            r.note = "검색 결과가 없습니다."
        return r


def change_label(ch: dict[str, Any]) -> str:
    """정정 변경 1건 → 사람이 읽는 말(확인 카드 · 이력)."""
    item, before, after = ch.get("item", ""), ch.get("before"), ch.get("after")
    if ch.get("target") == "village":
        what = {
            "evacuated": "대피 완료",
            "evac_order": "대피명령",
            "pre_evac_done": "사전대피 완료",
        }.get(item, item)
        name = ch.get("target_name") or f"마을 {ch.get('target_id')}"
        return f"{name} {what} {'예' if after else '아니오'}(전: {'예' if before else '아니오'})"
    if ch.get("target") == "deployed":
        group, _, key = item.partition(".")
        unit = GROUP_UNITS.get(group, "")
        sub = "합계" if key == "total" else DEPLOYED_LABELS.get(group, {}).get(key, key)
        return f"투입 {GROUP_NAMES.get(group, group)}({sub}) {num(before)} → {num(after)}{unit}"
    if item == "disaster_sms_sent":
        return f"재난문자 송출 {'예' if after else '아니오'}"
    return f"{item} {before} → {after}"


@dataclass(frozen=True)
class ToolSpec:
    fn: Callable[[ToolBox, str], ToolResult]
    desc: str
    arg: str = ""  # 인자 설명(없으면 인자 없음)


TOOLS: dict[str, ToolSpec] = {
    "overview": ToolSpec(
        ToolBox.overview,
        "산불 개요: 이름 · 상태 · 발생 · 제안서 버전 · 현재/5시간 뒤 대응단계 · 현재 지휘권자 · 면적",
    ),
    "stage": ToolSpec(
        ToolBox.stage,
        "대응단계 판정: 면적 · 풍속 · 시설피해 요소별 단계, 격상 예상, 지휘권 인계 · 사유, 판정 근거 원문",
    ),
    "spread": ToolSpec(
        ToolBox.spread,
        "확산 예측: 1~8시간 뒤 예상 면적, 100 ha 넘는 시점, 주 확산 방향, 다음에 닿을 마을",
    ),
    "weather": ToolSpec(
        ToolBox.weather,
        "기상: 현재 풍향 · 풍속 · 기온 · 습도, 시간별 바람, 0~5시간 평균 풍속, 일몰 · 일출",
    ),
    "resources": ToolSpec(
        ToolBox.resources,
        "진화자원: 투입된 헬기 · 차량 · 인력(기관별), 현재 · 5시간 뒤 동원 범위, 기지",
    ),
    "villages": ToolSpec(
        ToolBox.villages,
        "마을 목록: 이미 닿음 · 5시간 · 8시간 안 마을의 도달 시각 · 인구 · 대피 상태",
    ),
    "village": ToolSpec(
        ToolBox.village,
        "마을 하나 상세: 도달 시각 · 인구 · 대피 상태 · 대피소 후보 · 문자 발송",
        "마을 이름",
    ),
    "shelters": ToolSpec(
        ToolBox.shelters, "대피소 후보(마을 · 거점, 거리 · 수용 인원)", "마을 이름(없으면 전체)"
    ),
    "facilities": ToolSpec(
        ToolBox.facilities,
        "취약시설(요양원 등) · 송전선 · 변전소 · 사찰 · 국가유산 · 5시간 범위 주택 수",
    ),
    "roads": ToolSpec(
        ToolBox.roads, "통제가 필요할 수 있는 큰 도로 구간 · 도달 시각 · 가까운 경찰서"
    ),
    "alerts": ToolSpec(
        ToolBox.alerts, "재난문자 · 방송 전파: 읍면별 Go/Set 단계, 발송 기록, 송출 여부"
    ),
    "agencies": ToolSpec(ToolBox.agencies, "관할 소방서 · 경찰서 · 가까운 응급의료기관"),
    "item": ToolSpec(
        ToolBox.item,
        "제안서 항목 하나: 판정 · 제안 문장 · 목록 · 빠진 정보 · 인용 근거 원문",
        "항목 코드(S1~S6 · E1~E6)",
    ),
    "proposal": ToolSpec(
        ToolBox.proposal, "제안서 전체 요약: 항목별 구분 · 제안 수, 발동하지 않은 항목"
    ),
    "changes": ToolSpec(ToolBox.changes, "직전 제안서 대비 바뀐 항목, 최근 상황 정정 이력"),
    "search": ToolSpec(
        ToolBox.search,
        "표준매뉴얼 · 법령 근거 검색(규정 · 기준 · 절차 · 이유를 물을 때)",
        "검색할 말",
    ),
}
