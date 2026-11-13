# ruff: noqa: E501
"""상황 판정 → 항목별 작성 지침을 고를 깃발(11/8 저녁 사용자 결정).

규칙 엔진 bundles에서 이번 회차의 상황을 읽어 참 · 거짓 깃발로 만든다. 깃발은 판정이지 제안이 아니다 —
어떤 지침 줄을 프롬프트에 넣을지(guides.py)만 정하고, 문장 내용과 근거는 LLM이 근거 검색으로 정한다.

    f = flags(bundles, items, common)
    f["large_now"], f["escalation"], f["commander_change"], f["night_arrival"], …
"""

from typing import Any

from agent.situate import stage_key

STAGE_NO = {"초기대응": 0, "1단계": 1, "2단계": 2, "3단계": 3}
LARGE_HA = 100  # 대형산불 = 피해면적 100 ha 이상(시행령 제21조① · 매뉴얼 p.114)
UPPER = ("도지사", "특별시장", "광역시장", "특별자치", "산림청장")


def _any_night(rows: list[dict[str, Any]]) -> bool:
    return any(r.get("night") and not r.get("passed") for r in rows or [])


def flags(
    bundles: dict[str, Any], items: list[dict[str, Any]], common: dict[str, Any]
) -> dict[str, Any]:
    b = bundles
    st, cm, area = b.get("stage", {}), b.get("commander", {}), b.get("area", {})
    now_no = STAGE_NO.get(stage_key(st.get("now", {}).get("stage")), 0)
    p5_no = STAGE_NO.get(stage_key(st.get("p5", {}).get("stage")), 0)
    now_ha = area.get("now_ha") or 0
    slices = area.get("slices") or []
    p5_ha = next((s.get("ha") for s in slices if s.get("p") == 5), now_ha) or 0
    cross = next((s for s in slices if (s.get("ha") or 0) >= LARGE_HA), None)
    vil = b.get("villages", {})
    vul = b.get("vulnerable", {})
    prot = b.get("protected", {})
    sh = b.get("shelters", {})
    w = b.get("weather", {})
    sun = (b.get("time", {}) or {}).get("sun", {}) or {}
    reader = common.get("reader", "")
    home = b.get("mobilization", {}).get("home") or (common.get("incident") or {}).get("sigungu")
    e2 = next((it for it in items if it["id"] == "E2"), {})
    imm = (e2.get("facts") or {}).get("immediate", []) or []
    all_vil = (vil.get("burning") or []) + (vil.get("p5") or []) + (vil.get("p8") or [])
    f = {
        "now_ha": now_ha,
        "p5_ha": p5_ha,
        "large_now": now_ha >= LARGE_HA,
        "large_soon": now_ha < LARGE_HA and cross is not None,  # 8시간 안에 대형산불 기준을 넘음
        "large_at": cross.get("until") if cross and now_ha < LARGE_HA else None,
        "small": now_ha < LARGE_HA and cross is None,
        "stage_now": st.get("now", {}).get("stage"),
        "stage_p5": st.get("p5", {}).get("stage"),
        "escalation": p5_no > now_no,
        "stage_up_from_prev": (st.get("change") or {}).get("type") == "up",
        "commander_now": (cm.get("now") or {}).get("title"),
        "commander_next": (cm.get("next") or {}).get("title"),
        "commander_change": (cm.get("now") or {}).get("title")
        != (cm.get("next") or {}).get("title"),
        "reader_upper": any(u in reader for u in UPPER),
        "now_is_night": bool(sun.get("now_is_night")),
        "sunset_in_range": (sun.get("sunset") or {}).get("zone") in ("p5", "p8")
        if isinstance(sun.get("sunset"), dict)
        else False,
        "night_arrival": _any_night(all_vil) or _any_night(vul.get("in_range") or []),
        "wind_now_ms": (w.get("now") or {}).get("ms"),
        "wind_max_ms": (w.get("max") or {}).get("ms"),
        "wind_rising": ((w.get("max") or {}).get("ms") or 0)
        >= ((w.get("now") or {}).get("ms") or 0) + 1.5,
        "villages_burning": len(vil.get("burning") or []),
        "villages_p5": len(vil.get("p5") or []),
        "villages_p8": len(vil.get("p8") or []),
        "evac_order_missing": any(not v.get("evac_order") for v in imm),
        "evac_unconfirmed": any(v.get("alert_sent_at") or v.get("evac_order") for v in imm)
        and any(not v.get("evacuated") for v in imm),
        "vulnerable_p5": (vul.get("counts") or {}).get("within_p5", 0) > 0,
        "vulnerable_p8": (vul.get("counts") or {}).get("p8", 0) > 0,
        "vulnerable_daytime": (vul.get("counts") or {}).get("daytime_only", 0) > 0,
        "power_lines": bool(prot.get("power_lines")),
        "heritage": bool((prot.get("heritage") or {}).get("in_p8")) or bool(prot.get("temples")),
        "roads_at_risk": bool((b.get("roads") or {}).get("sections")),
        "shelters_no_options": (sh.get("counts") or {}).get("no_options", 0) > 0,
        "shelters_in_range": (sh.get("counts") or {}).get("in_p8", 0) > 0,
        "other_sigungu": any(v.get("sigungu") and v.get("sigungu") != home for v in all_vil),
        "alerts_changed": any(a.get("change") for a in (b.get("alerts") or {}).get("areas") or []),
        "alerts_removed": bool((b.get("alerts") or {}).get("removed")),
        "requests_s6": _requests(items, "S6"),
        "requests_e6": _requests(items, "E6"),
    }
    return f


def _requests(items: list[dict[str, Any]], iid: str) -> int:
    it = next((x for x in items if x["id"] == iid), {})
    return int((it.get("facts") or {}).get("request_count") or 0)


def summary(f: dict[str, Any]) -> str:
    """프롬프트에 넣는 상황 한 줄(판정 값만)."""
    if f["large_now"]:
        size = f"대형산불(지금 {f['now_ha']} ha, 100 ha 이상)"
    elif f["large_soon"]:
        size = f"지금 {f['now_ha']} ha, {f['large_at']} 무렵 대형산불 기준 100 ha를 넘을 수 있음"
    else:
        size = f"중소형 산불(지금 {f['now_ha']} ha, 8시간 안에 100 ha를 넘지 않음)"
    parts = [
        f"규모: {size}, 5시간 뒤 {f['p5_ha']} ha",
        f"단계: 지금 {f['stage_now']} → 5시간 뒤 {f['stage_p5']}"
        + (" (오름)" if f["escalation"] else " (그대로)"),
        f"지휘권자: {f['commander_now']}"
        + (f" → {f['commander_next']} (바뀜)" if f["commander_change"] else " (그대로)"),
        "지금 밤"
        if f["now_is_night"]
        else ("지금 낮, 8시간 안에 일몰" if f["sunset_in_range"] else "8시간 안은 낮"),
        f"바람: 지금 {f['wind_now_ms']} m/s, 최대 {f['wind_max_ms']} m/s"
        + (" (강해짐)" if f["wind_rising"] else ""),
    ]
    return " · ".join(parts)
