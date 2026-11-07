"""S3 진화자원 현황 · 동원 범위: 엔진 JSON 묶음에서 S3 입력을 만든다.

판정하지 않는다. `resources`(투입) · `mobilization`(범위 · 기지)을 옮긴다. 「집중 투입」 ·
「응원 요청 준비」 · 초기 헬기 2대 원칙(p.72)은 LLM이 투입 수와 근거 청크를 보고 쓴다(11/7).
"""

from typing import Any

ID, NAME = "S3", "진화자원 현황·동원 범위"
STATUS = {"up": "scope_expands", "same": "same_scope", "down": "same_scope"}


def _base(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r[k] for k in ("name", "kind", "sigungu", "jurisdiction", "km", "available")}


def _scope_text(s: dict[str, Any]) -> str:
    """「지상 의성군 100% + 인접 시군 50% 이내 · 공중 발화지 반경 100 km」."""
    g = s["ground"]
    ground = f"{g['home']['name']} {g['home']['share']}"
    extra = g["extra"]
    if extra["kind"] == "neighbors":
        ground += f" + 인접 시군 {extra['share']}"
    elif extra["kind"] == "sido":
        ground = f"{extra['names'][0]} {extra['share']}"
    air = "전국" if s["air"]["kind"] == "national" else f"발화지 반경 {s['air']['km']} km"
    return f"지상 {ground} · 공중 {air}"


def s3_input(bundles: dict[str, Any]) -> dict[str, Any]:
    """S3 입력을 만든다. 늘 만든다(범위는 늘 있음).

    1. 투입 = `resources` 그대로(라벨 고정)
    2. 범위 = `mobilization.now` · `p5`
    3. 부를 수 있는 기지: 지금 범위 안(now) / 5시간 뒤 새로 드는 곳(p5) — 이름표로 거르기
    4. 상황 = 단계 추세 이름 바꾸기(범위는 단계를 따름)
    """
    res, mob = bundles["resources"], bundles["mobilization"]
    facts = {
        "deployed": {k: res[k] for k in ("helicopter", "crew", "vehicle")},  # 1
        "scope": {"now": mob["now"], "p5": mob["p5"]},  # 2
        "available_now": [_base(r) for r in mob["bases"] if r["first_scope"] == "now"],  # 3
        "added_at_p5": [_base(r) for r in mob["bases"] if r["first_scope"] == "p5"],
    }
    status = STATUS[bundles["stage"]["trend"]]  # 4
    return {
        "id": ID,
        "name": NAME,
        "status": status,
        "finding": _finding(facts, status),
        "facts": facts,
        "targets": [],
        "withheld": res["unknown"] + mob["unknown"],
        "rule_basis": mob["basis"],
    }


def _finding(f: dict[str, Any], status: str) -> str:
    """「투입: 헬기 23 · 장비 26 · 인력 151 / 현재 1단계 → 5시간 뒤 2단계: 동원 범위 … 로 확대」."""
    d = f["deployed"]
    if d["crew"]["total"] is None:
        head = "투입: 상황 보고 전"
    else:
        head = (
            f"투입: 헬기 {d['helicopter']['total']} · 장비 {d['vehicle']['total']}"
            f" · 인력 {d['crew']['total']}"
        )
    now, p5 = f["scope"]["now"], f["scope"]["p5"]
    if status == "scope_expands":
        tail = f"현재 {now['stage']} → 5시간 뒤 {p5['stage']}: 동원 범위 {_scope_text(p5)}으로 확대"
    else:
        tail = f"현재 {now['stage']} 동원 범위: {_scope_text(now)}"
    return f"{head} / {tail}"
