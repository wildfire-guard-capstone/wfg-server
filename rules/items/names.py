"""마을 이름 쓰기(조립기 공통): 법정리 줄에 닿는 행정리를 괄호로 붙인다(11/19 사용자 결정 D2)."""

from collections.abc import Sequence
from typing import Any


def village_label(name: str, admin: Sequence[str] | None = None) -> str:
    """「신안리(신안1리)」 · 「도덕리(도덕1리·도덕2리)」. 닿는 행정리가 없으면 법정리 이름만."""
    return f"{name}({'·'.join(admin)})" if admin else name


def admin_names(row: dict[str, Any]) -> list[str]:
    """줄의 닿는 행정리 이름(없으면 빈 목록). 묶음 줄(행정리 칸이 사전) · 항목 줄(이름) 모두."""
    return [a["name"] if isinstance(a, dict) else a for a in row.get("admin_villages") or []]


def label_of(row: dict[str, Any]) -> str:
    """마을 줄 → 「신안리(신안1리)」."""
    return village_label(row["name"], admin_names(row))
