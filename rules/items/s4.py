"""S4 진화 방식 · 시간대 전략: 엔진 JSON 묶음에서 S4 입력을 만든다.

판정하지 않는다. `time.sun`(지금 밤인가 · 일몰 · 일출이 드는 P)과 `weather`(시간별 · 최대)를 옮긴다.
주간 진화 · 야간 진화 준비 · 지상 중심 전환 · 헬기 재투입은 LLM이 p.77 · 78로 쓴다
(11/7 — 옛 status night_coming(5시간 안 일몰, D)은 뺌: 일몰이 드는 구간을 사실로 넘김).
"""

from typing import Any

ID, NAME = "S4", "진화 방식·시간대 전략"


def s4_input(bundles: dict[str, Any]) -> dict[str, Any]:
    """S4 입력을 만든다. 늘 만든다.

    1. 해: 일몰 · 일출 시각과 그 시각이 드는 구간, 지금 밤인가
    2. 바람: 현재 · 시간별 · 최대(최대인 시각의 처음 ~ 끝을 「14:50~16:50」으로)
    3. 상황 = 지금 밤인가 이름 바꾸기(day · night)
    """
    sun, w = bundles["time"]["sun"], bundles["weather"]
    hours = w["max"]["hours"]
    label = hours[0] if len(hours) == 1 else f"{hours[0]}~{hours[-1]}"
    facts = {
        "sun": {  # 1
            "sunset": sun["sunset"]["at"],
            "sunset_zone": sun["sunset"]["zone"],
            "sunrise": sun["sunrise"]["at"],
            "now_is_night": sun["now_is_night"],
        },
        "now": {  # 2
            "wind_dir": w["now"]["dir"],
            "wind_ms": w["now"]["ms"],
            "temp_c": w["now"]["temp_c"],
            "rh_pct": w["now"]["rh_pct"],
        },
        "wind": [{"at": r["at"], "dir": r["dir"], "ms": r["ms"]} for r in w["hourly"]],
        "max": {"ms": w["max"]["ms"], "dir": w["max"]["dir"], "label": label},
    }
    status = "night" if sun["now_is_night"] else "day"  # 3
    when = "지금 밤" if status == "night" else "지금 낮"
    finding = (
        f"{when} · 일몰 {facts['sun']['sunset']} · 일출 {facts['sun']['sunrise']}"
        f" · 최대 풍속 {facts['max']['ms']} m/s({facts['max']['dir']}, {label})"
    )
    return {
        "id": ID,
        "name": NAME,
        "status": status,
        "finding": finding,
        "facts": facts,
        "targets": [],
        "withheld": w["unknown"],
        "rule_basis": ["SM-p077", "SM-p078"],
    }
