"""E2 대피 대상 · 순서 · 시점: 엔진 JSON 묶음에서 E2 입력을 만든다.

판정하지 않는다. `villages`(구간 · 닿는 시각 · 밤 · 인구 · 현장 입력)와 `time.sun`에서 고른다.
누구를(전 주민 / 고령자) 언제까지(「일몰 전」 등) 대피시킬지는 LLM이 p.75 · 76 · 79로 정한다
(11/7 — 옛 R-08 대피 인원 · R-06 시한은 엔진에서 뺌). P8 안에 마을이 없으면 E2를 만들지 않는다.
"""

from typing import Any

ID, NAME = "E2", "대피 대상·순서·시점"


def _row(r: dict[str, Any]) -> dict[str, Any]:
    """마을 한 줄: 현장 입력을 펼쳐 옮긴다(옮겨 담기)."""
    f = r["field"]
    return {
        "name": r["name"],
        "emd": r["emd"],
        "zone": r["zone"],
        "people": r["population"],
        "elderly": r["pop65_est"],
        "from": r["from"],
        "until": r["until"],
        "night": r["night"],
        "passed": r["passed"],
        "evac_order": f["evac_order"],
        "evacuated": f["evacuated"],
        "pre_evac_done": f["pre_evac_done"],
        "alert_sent_at": f["alert_at"],
        "reported_at": f["reported_at"],
    }


def e2_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """E2 입력을 만든다. 마을이 없으면 None.

    1. 위험구역(이미 닿음 + 5시간 안) 마을 · 잠재 위험구역 마을을 고른다
    2. 위험구역 인구 합 · 일몰 시각을 옮긴다
    3. 상황: 대피명령이 안 된 위험구역 마을이 있으면 order_needed, 없고 사전대피를 마치지 않은
       잠재 마을이 있으면 pre_evac_needed, 그 밖 all_reported(현장 입력 이름표로 거르기)
    4. finding 틀 — 사실만(「일몰 전」 같은 시한은 쓰지 않음)
    """
    v, sun = bundles["villages"], bundles["time"]["sun"]
    immediate = [_row(r) for r in v["burning"] + v["p5"]]  # 1
    pre_evac = [_row(r) for r in v["p8"]]
    if not immediate and not pre_evac:
        return None
    facts = {
        "immediate": immediate,
        "pre_evac": pre_evac,
        "immediate_total": v["totals"]["within_p5"]["population"],  # 2
        "sunset": sun["sunset"]["at"],
        "now_is_night": sun["now_is_night"],
    }
    if any(not r["evac_order"] for r in immediate):  # 3
        status = "order_needed"
    elif any(not r["pre_evac_done"] for r in pre_evac):
        status = "pre_evac_needed"
    else:
        status = "all_reported"
    return {
        "id": ID,
        "name": NAME,
        "status": status,
        "finding": _finding(v, pre_evac, sun),
        "facts": facts,
        "targets": [f"village:{r['id']}" for z in ("burning", "p5", "p8") for r in v[z]],
        "withheld": v["unknown"],
        "rule_basis": ["SM-p075", "SM-p076", "SM-p079"],
    }


def _finding(v: dict[str, Any], pre_evac: list[dict[str, Any]], sun: dict[str, Any]) -> str:
    """「위험구역 7곳 1,523명 / 잠재 위험구역 1곳 114명: 정자리(밤에 닿음, 일몰 18:38)」."""
    t = v["totals"]
    parts = []
    if t["within_p5"]["count"]:
        parts.append(f"위험구역 {t['within_p5']['count']}곳 {t['within_p5']['population']:,}명")
    if pre_evac:
        names = ", ".join(r["name"] + ("(밤에 닿음)" if r["night"] else "") for r in pre_evac)
        parts.append(f"잠재 위험구역 {t['p8']['count']}곳 {t['p8']['population']:,}명: {names}")
    tail = f" · 일몰 {sun['sunset']['at']}" if any(r["night"] for r in pre_evac) else ""
    return " / ".join(parts) + tail
