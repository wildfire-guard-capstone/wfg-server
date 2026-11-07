"""E5 재난문자 · 방송 전파: 엔진 JSON 묶음에서 E5 입력을 만든다.

판정하지 않는다. `alerts`(읍면 Go · Set · 변화 · 문자 기록)를 옮긴다. 송출 요청 · 함께 쓸 전파
수단은 LLM이 p.76 · 78로 쓴다. 문안은 쓰지 않는다. P8 안 마을이 없으면 E5를 만들지 않는다.
"""

from typing import Any

ID, NAME = "E5", "재난문자·방송 전파"
CHANGED = ("new", "up")


def e5_input(bundles: dict[str, Any]) -> dict[str, Any] | None:
    """E5 입력을 만든다. 읍면이 없으면 None.

    1. 읍면 줄을 옮긴다(`level` → `stage` 이름 바꾸기)
    2. 상황: 처음 들어오거나 오른(new · up) 읍면이 있으면 changed, 아니면 unchanged
    3. finding 틀
    """
    al = bundles["alerts"]
    if not al["areas"]:
        return None
    areas = [  # 1
        {
            "sigungu": a["sigungu"],
            "emd": a["emd"],
            "stage": a["level"],
            "villages": a["villages"],
            "change": a["change"],
            "prev": a["prev"],
        }
        for a in al["areas"]
    ]
    changed = any(a["change"] in CHANGED for a in areas)  # 2
    return {
        "id": ID,
        "name": NAME,
        "status": "changed" if changed else "unchanged",
        "finding": _finding(areas, al["removed"]),  # 3
        "facts": {"areas": areas, "removed": al["removed"], "alert_log": al["log"]},
        "targets": [],
        "withheld": [{"field": "문안", "reason": "사람이 작성"}],
        "rule_basis": al["basis"],
    }


def by_stage(areas: list[dict[str, Any]], stage: str) -> str:
    """「안평면(괴산리·신월리) · 의성읍(철파리)」."""
    return " · ".join(
        f"{a['emd']}({'·'.join(a['villages'])})" for a in areas if a["stage"] == stage
    )


def _finding(areas: list[dict[str, Any]], removed: list[dict[str, Any]]) -> str:
    """「Go: 안평면(…) · … / Set: 옥산면(정자리) / 바뀐 곳: 모두 새로 들어옴」."""
    parts = []
    for stage, label in (("go", "Go"), ("set", "Set")):
        listed = by_stage(areas, stage)
        if listed:
            parts.append(f"{label}: {listed}")
    if all(a["change"] == "new" for a in areas) and not removed:
        parts.append("바뀐 곳: 모두 새로 들어옴")
    else:
        moved = [
            f"{a['emd']}({a['prev'] or '없음'} → {a['stage']})"
            for a in areas
            if a["change"] != "same"
        ]
        moved += [f"{r['emd']}({r['prev']} → 범위 밖)" for r in removed]
        if moved:
            parts.append(f"바뀐 곳: {' · '.join(moved)}")
    return " / ".join(parts)
