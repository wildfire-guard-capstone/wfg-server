"""엔진 JSON 묶음: 공통 결과(ctx)를 주제별 고정 모양 JSON으로 바꾼다(11/7).

엔진은 항목을 모른다. 묶음은 사실과 기준 판정만 담고, 할 일의 이름(협의 · 인계 등)은
붙이지 않는다 — 그것은 LLM이 매뉴얼로 정한다. 항목 입력(items/)은 이 묶음에서
고르기 · 옮겨 담기 · 이름 바꾸기 · 틀 채우기만 한다.

고정 모양: 키는 늘 있다. 없으면 `[]` · `0` · `false` · `null`. 묶음마다 `basis`(판정 근거
청크 ID) · `unknown`(모르는 칸과 이유)을 둔다. 묶음 하나 = 파일 하나.
"""

from typing import Any

from rules.bundles.agencies import agencies_bundle
from rules.bundles.alerts import alerts_bundle
from rules.bundles.area import area_bundle
from rules.bundles.commander import commander_bundle
from rules.bundles.houses import houses_bundle
from rules.bundles.mobilization import mobilization_bundle
from rules.bundles.protected import protected_bundle
from rules.bundles.resources import resources_bundle
from rules.bundles.roads import roads_bundle
from rules.bundles.shelters import shelters_bundle
from rules.bundles.spread import spread_bundle
from rules.bundles.stage import stage_bundle
from rules.bundles.timing import time_bundle
from rules.bundles.villages import villages_bundle
from rules.bundles.vulnerable import vulnerable_bundle
from rules.bundles.weather import weather_bundle
from rules.context import Context


def build_bundles(ctx: Context) -> dict[str, Any]:
    """공통 결과를 엔진 JSON(주제별 묶음)으로 바꾼다. `proposals.context`에 그대로 저장한다."""
    return {
        "time": time_bundle(ctx),
        "area": area_bundle(ctx),
        "stage": stage_bundle(ctx),
        "commander": commander_bundle(ctx),
        "villages": villages_bundle(ctx),
        "vulnerable": vulnerable_bundle(ctx),
        "protected": protected_bundle(ctx),
        "houses": houses_bundle(ctx),
        "shelters": shelters_bundle(ctx),
        "roads": roads_bundle(ctx),
        "alerts": alerts_bundle(ctx),
        "mobilization": mobilization_bundle(ctx),
        "resources": resources_bundle(ctx),
        "weather": weather_bundle(ctx),
        "spread": spread_bundle(ctx),
        "agencies": agencies_bundle(ctx),
    }
