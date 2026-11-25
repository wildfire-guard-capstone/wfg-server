"""마을 줄 쓰기(조립기 공통): 이름 「법정리(행정리)」와 불이 닿는 · 다가오는 때.

이름은 한 덩어리로 넘긴다(11/25). 법정리와 행정리를 두 칸으로 나눠 주면 LLM이 한쪽만 쓰는 일이 잦다
(시험: 같은 꼴을 지킨 비율 29~48% → 한 덩어리 81~96%).
"""

from collections.abc import Sequence
from typing import Any


def village_label(name: str, admin: Sequence[str] | None = None) -> str:
    """「신안리(신안1리)」 · 「도덕리(도덕1리, 도덕2리)」. 행정리가 없으면 법정리만."""
    return f"{name}({', '.join(admin)})" if admin else name


def label_of(row: dict[str, Any]) -> str:
    """묶음의 마을 줄 → 「신안리(신안1리)」."""
    return village_label(row["name"], [a["name"] for a in row.get("admin_villages") or []])


def approach(row: dict[str, Any]) -> dict[str, Any]:
    """불이 실제로 닿는지 · 닿지 않으면 다가오는 거리(있는 칸만 옮김)."""
    return {k: row[k] for k in ("touched", "approach_km") if k in row}


def approaching(row: dict[str, Any]) -> bool:
    """불이 닿지 않고 다가오기만 하는 대상인가."""
    return row.get("touched") is False


def when(row: dict[str, Any]) -> str:
    """「지금(이미 닿음)」 · 「18:00 ~ 19:00 닿을 수 있음」 · 「19:00에 불이 0.2 km까지 접근」."""
    if row["zone"] == "burning":
        return "지금(이미 닿음)"
    night = "(밤)" if row["night"] else ""
    if approaching(row):
        return f"{row['until']}에 불이 {row['approach_km']} km까지 접근{night}"
    return f"{row['from']} ~ {row['until']} 닿을 수 있음{night}"
