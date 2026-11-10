# ruff: noqa: E501
"""어시스턴트가 보는 것 하나(`Context`): 산불 · 지금 상황 · 보고 있는 제안서(엔진 묶음 + 항목) ·
직전 제안서 · 최근 정정 · 마을 목록.

앱은 DB에서(app/services/assistant.py), 데모는 의성 데모 결과 폴더에서(`from_round`) 만든다.
값은 DB에 저장된 모양 그대로다(proposals.context = 엔진 묶음, proposal_items = 에이전트 결과).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# incidents.situation.deployed 키 ↔ 엔진 라벨(app/repositories/engine_inputs.DEPLOYED_LABELS와 같음)
DEPLOYED_LABELS: dict[str, dict[str, str]] = {
    "helicopter": {
        "forest": "산림청",
        "local": "지자체",
        "fire": "소방",
        "military": "군",
        "police": "경찰",
    },
    "crew": {
        "special_crew": "진화대",
        "forest_officials": "공무원",
        "fire": "소방",
        "military": "군",
        "police": "경찰",
        "other": "기타",
    },
    "vehicle": {"forest_vehicle": "지휘·진화차", "fire_truck": "소방차", "other": "기타"},
}
GROUP_NAMES = {"helicopter": "헬기", "vehicle": "차량", "crew": "인력"}
GROUP_UNITS = {"helicopter": "대", "vehicle": "대", "crew": "명"}
ITEM_NAMES = {
    "S1": "대응단계 판단·격상 검토·지휘권 인계",
    "S2": "진화 우선순위·보호 대상",
    "S3": "진화자원 투입·동원 범위",
    "S4": "주간·야간 진화 전략",
    "S5": "시설 보호",
    "S6": "대책회의·관계기관 협조",
    "E1": "위험구역 설정",
    "E2": "대피 순서·시점",
    "E3": "취약시설 조치",
    "E4": "대피소 후보",
    "E5": "도로 통제 요청",
    "E6": "재난문자·방송 전파",
}


@dataclass
class Context:
    """질문 하나에 답할 때 쓰는 자료 전부. 없는 것은 빈 값(예측 전이면 proposal None · bundles {})."""

    incident: dict[str, Any]  # id · name · sigungu · emd · address · status(진행 중 · 종료) · …
    reader: str  # 읽는 사람(통합지휘권자 직위, 예: 의성군수)
    situation: dict[str, Any] = field(default_factory=dict)  # incidents.situation(지금 값)
    proposal: dict[str, Any] | None = None  # id · version · trigger · created_at · base_at
    bundles: dict[str, Any] = field(default_factory=dict)  # proposals.context(엔진 묶음)
    items: list[dict[str, Any]] = field(default_factory=list)  # proposal_items 행
    previous: dict[str, Any] | None = None  # 직전 제안서: version · items[{item_code, …}]
    corrections: list[dict[str, Any]] = field(default_factory=list)  # 최근 정정(새것 먼저)
    villages: list[dict[str, Any]] = field(default_factory=list)  # 정정 대상: id · name · emd
    capacity: dict[str, int | None] = field(default_factory=dict)  # 투입 수 상한(모르면 없음)
    revision: str = "rev-0"  # 상황 버전(정정할 때마다 바뀜)

    @property
    def ended(self) -> bool:
        return self.incident.get("status") in ("종료", "ENDED")

    @property
    def has_proposal(self) -> bool:
        return self.proposal is not None and bool(self.bundles)

    def item(self, code: str) -> dict[str, Any] | None:
        code = (code or "").upper()
        return next((i for i in self.items if i.get("item_code") == code), None)

    def item_codes(self) -> list[str]:
        return [i["item_code"] for i in self.items]

    def chunk(self, chunk_id: str) -> dict[str, Any] | None:
        """제안서 항목이 인용한 원문 가운데 하나(근거 서버 없이 찾음)."""
        for item in self.items:
            for c in item.get("evidence") or []:
                if c.get("chunk_id") == chunk_id:
                    return c
        return None

    def zone_villages(self) -> list[dict[str, Any]]:
        """엔진 묶음의 마을(이미 닿음 · 5시간 · 8시간), 닿는 순."""
        v = self.bundles.get("villages") or {}
        return [x for zone in ("burning", "p5", "p8") for x in v.get(zone) or []]

    def village_field(self, village_id: Any) -> dict[str, Any]:
        """마을 현장 입력: 지금 상황(정정 반영)이 먼저, 없으면 제안서 당시 값."""
        live = (self.situation.get("villages") or {}).get(str(village_id))
        if live is not None:
            return live
        for v in self.zone_villages():
            if str(v.get("id")) == str(village_id):
                return v.get("field") or {}
        return {}


# ── 데모 결과 폴더에서 ─────────────────────────────────────────────


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def paragraphs(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for s in sections for b in s.get("blocks", []) if b.get("type") == "paragraph"]


def item_row(written: dict[str, Any], engine_item: dict[str, Any]) -> dict[str, Any]:
    """에이전트 결과 + 엔진 항목 → proposal_items 행 모양(repositories/proposals.save_proposal과 같음)."""
    paras = paragraphs(written.get("sections") or [])
    order = ["즉시", "대기", "협의", "요청"]
    cats = {p.get("category") for p in paras}
    return {
        "item_code": written["id"],
        "name": written.get("name") or engine_item.get("name") or ITEM_NAMES.get(written["id"]),
        "state": written.get("state", "ok"),
        "categories": [c for c in order if c in cats],
        "sections": written.get("sections") or [],
        "evidence": written.get("evidence") or [],
        "text": "\n".join(p.get("body", "") for p in paras),
        "detail": engine_item,
    }


def situation_from_bundles(bundles: dict[str, Any]) -> dict[str, Any]:
    """엔진 묶음 → incidents.situation 모양(데모용: DB가 없을 때 제안서 당시 값을 지금 값으로)."""
    out: dict[str, Any] = {"villages": {}}
    for v in (bundles.get("villages") or {}).get("burning", []) + (
        (bundles.get("villages") or {}).get("p5", [])
        + (bundles.get("villages") or {}).get("p8", [])
    ):
        f = {k: val for k, val in (v.get("field") or {}).items() if val not in (None, False)}
        if f:
            out["villages"][str(v["id"])] = f
    res = bundles.get("resources") or {}
    deployed: dict[str, Any] = {}
    for group, labels in DEPLOYED_LABELS.items():
        g = res.get(group)
        if not g:
            continue
        back = {lab: key for key, lab in labels.items()}
        values = {back[b["label"]]: b["n"] for b in g.get("by", []) if b.get("label") in back}
        deployed[group] = {"total": g.get("total", sum(values.values())), **values}
    if deployed:
        out["deployed"] = deployed
    return out


def from_round(round_dir: str | Path, villages: list[dict[str, Any]] | None = None) -> Context:
    """의성 데모 회차 폴더(runs/<이름>/round_k: 02_rules/llm_input.json + 05_output/proposal.json)."""
    d = Path(round_dir)
    llm_input = _load(d / "02_rules" / "llm_input.json")
    written = _load(d / "05_output" / "proposal.json")
    written = written["items"] if isinstance(written, dict) else written
    engine = {it["id"]: it for it in llm_input["items"]}
    bundles, common = llm_input["bundles"], llm_input["common"]
    prop = common.get("proposal", {})
    items = [item_row(w, engine.get(w["id"], {})) for w in written]
    zone = [
        {"id": v["id"], "name": v["name"], "emd": v.get("emd"), "sigungu": v.get("sigungu")}
        for z in ("burning", "p5", "p8")
        for v in (bundles.get("villages") or {}).get(z, [])
    ]
    seen = {str(v["id"]) for v in zone}
    directory = zone + [v for v in villages or [] if str(v["id"]) not in seen]
    return Context(
        incident={
            "id": None,
            **common.get("incident", {}),
            "current_stage": common.get("current_stage"),
        },
        reader=common.get("reader", ""),
        situation=situation_from_bundles(bundles),
        proposal={
            "id": None,
            "version": prop.get("version"),
            "trigger": prop.get("trigger", "예측 갱신"),
            "created_at": prop.get("generated_at"),
            "base_at": prop.get("base_at"),
        },
        bundles=bundles,
        items=items,
        villages=directory,
    )
