# ruff: noqa: E501
"""어시스턴트 시험용 작은 상황: 의성 데모 12:50 회차(tune15)의 엔진 묶음 · 제안서를 줄인 것.

마을 id는 시험용 값이다. 하화리는 안평면 · 옥산면 두 곳에 있게 해 「모호한 대상」을 시험한다.
"""

import copy

from assistant.context import Context

VILLAGE = {
    "evac_order": False,
    "evacuated": False,
    "pre_evac_done": False,
    "alert_at": None,
    "reported_at": None,
}

BUNDLES = {
    "time": {
        "base_at": "2025-03-22 12:50",
        "generated_at": "2025-03-22 12:50",
        "sun": {
            "sunset": {"at": "18:38", "p": 6, "zone": "p8"},
            "sunrise": {"at": "다음 날 06:27"},
            "now_is_night": False,
        },
    },
    "area": {
        "now_ha": 48.5,
        "slices": [
            {"p": 1, "until": "13:50", "ha": 80.1},
            {"p": 2, "until": "14:50", "ha": 119.7},
            {"p": 5, "until": "17:50", "ha": 301.5},
            {"p": 8, "until": "20:50", "ha": 435.7},
        ],
        "first_over": [{"ha": 100, "p": 2, "until": "14:50", "area_ha": 119.7}],
        "unknown": [],
    },
    "stage": {
        "now": {
            "stage": "1단계",
            "area_ha": 48.5,
            "wind_ms": 3.4,
            "facility": 1,
            "facility_is_minimum": True,
            "by_area": "1단계",
            "by_wind": "1단계",
            "by_facility": "1단계",
            "decided_by": ["area", "wind", "facility"],
        },
        "p5": {
            "stage": "2단계",
            "area_ha": 301.5,
            "wind_ms": 5.5,
            "facility": 1,
            "facility_is_minimum": True,
            "by_area": "2단계",
            "by_wind": "1단계",
            "by_facility": "1단계",
            "decided_by": ["area"],
        },
        "change": {"prev": None, "now": "1단계", "type": "new"},
        "basis": ["SM-p073"],
        "unknown": [{"field": "진화예상시간", "reason": "MVP에서 제외"}],
    },
    "commander": {
        "reader": "의성군수",
        "now": {"title": "의성군수", "level": "sigungu", "reasons": []},
        "next": {
            "title": "경상북도지사",
            "level": "sido",
            "reasons": [{"kind": "area", "ha": 100, "text": "피해면적 100 ha 이상 대형산불"}],
        },
        "basis": ["SM-p072"],
    },
    "villages": {
        "burning": [
            {
                "id": 101,
                "name": "괴산리",
                "emd": "안평면",
                "sigungu": "의성군",
                "first_p": 0,
                "zone": "burning",
                "from": None,
                "until": "지금",
                "passed": False,
                "night": False,
                "population": 147,
                "pop65_est": None,
                "field": dict(VILLAGE),
            },
            {
                "id": 102,
                "name": "신월리",
                "emd": "안평면",
                "sigungu": "의성군",
                "first_p": 0,
                "zone": "burning",
                "from": None,
                "until": "지금",
                "passed": False,
                "night": False,
                "population": 128,
                "pop65_est": None,
                "field": {**VILLAGE, "alert_at": "12:40"},
            },
        ],
        "p5": [
            {
                "id": 103,
                "name": "신안리",
                "emd": "안평면",
                "sigungu": "의성군",
                "first_p": 4,
                "zone": "p5",
                "from": "15:50",
                "until": "16:50",
                "passed": False,
                "night": False,
                "population": 181,
                "pop65_est": None,
                "field": dict(VILLAGE),
            },
        ],
        "p8": [
            {
                "id": 104,
                "name": "박곡리",
                "emd": "안평면",
                "sigungu": "의성군",
                "first_p": 8,
                "zone": "p8",
                "from": "19:50",
                "until": "20:50",
                "passed": False,
                "night": True,
                "population": 280,
                "pop65_est": None,
                "field": dict(VILLAGE),
            },
        ],
        "totals": {
            "burning": {"count": 2, "population": 275},
            "p5": {"count": 1, "population": 181},
            "p8": {"count": 1, "population": 280},
        },
        "unknown": [{"field": "장애인 · 어린이 인원", "reason": "리 단위 자료 없음"}],
    },
    "vulnerable": {"in_range": [], "counts": {"within_p5": 0}, "basis": [], "unknown": []},
    "protected": {
        "power_lines": [
            {
                "id": 3,
                "name": "154 kV 송전선 괴산리 구간",
                "first_p": 3,
                "zone": "p5",
                "from": "14:50",
                "until": "15:50",
                "passed": False,
                "night": False,
                "km_p5": 1.3,
            }
        ],
        "substations": [],
        "temples": [],
        "heritage": {"total": 46, "in_p8": 0, "items": []},
        "unknown": [],
    },
    "houses": {
        "is_minimum": True,
        "within_p5": {
            "total": 1,
            "by_village": [{"village": "신월리", "emd": "안평면", "houses": 1}],
        },
    },
    "shelters": {
        "villages": [
            {
                "village": "괴산리",
                "emd": "안평면",
                "zone": "burning",
                "until": "지금",
                "population": 147,
                "village_sites": [
                    {
                        "name": "안평면분회경로당",
                        "kind": "경로당",
                        "safety": "safe",
                        "km": 0.9,
                        "capacity": 48,
                    }
                ],
                "hub_sites": [
                    {
                        "name": "안평초등학교",
                        "kind": "학교",
                        "safety": "safe",
                        "km": 1.1,
                        "capacity": 109,
                    }
                ],
                "excluded": [],
            },
        ],
        "basis": [],
        "unknown": [],
    },
    "roads": {"sections": [], "basis": [], "unknown": []},
    "alerts": {
        "areas": [
            {
                "sigungu": "의성군",
                "emd": "안평면",
                "level": "go",
                "villages": ["괴산리", "신월리", "신안리", "박곡리"],
                "change": "new",
            }
        ],
        "removed": [],
        "log": [{"village": "신월리", "emd": "안평면", "at": "12:40", "kind": "대피 문자"}],
        "basis": [],
    },
    "mobilization": {
        "home": "의성군",
        "now": {
            "stage": "1단계",
            "ground": {
                "home": {"name": "의성군", "share": "100%"},
                "extra": {"kind": "neighbors", "names": ["안동시", "청송군"], "share": "50% 이내"},
            },
            "air": {"kind": "radius", "km": 100},
        },
        "p5": {
            "stage": "2단계",
            "ground": {
                "home": {"name": "의성군", "share": "100%"},
                "extra": {"kind": "sido", "names": ["경상북도"], "share": "100%"},
            },
            "air": {"kind": "national", "km": None},
        },
        "bases": [
            {
                "name": "의성군 임차헬기(서부)",
                "kind": "지자체 임차헬기",
                "jurisdiction": "관할",
                "km": 14.2,
                "available": {"helicopters": None},
            }
        ],
        "basis": [],
        "unknown": [{"field": "추가 투입 대수와 배치 위치", "reason": "산정 기준 없음"}],
    },
    "resources": {
        "source": "상황 보고 입력",
        "reported_at": "12:50",
        "helicopter": {
            "total": 23,
            "by": [
                {"label": "산림청", "n": 5},
                {"label": "지자체", "n": 16},
                {"label": "소방", "n": 2},
                {"label": "군", "n": 0},
                {"label": "경찰", "n": 0},
            ],
        },
        "crew": {
            "total": 151,
            "by": [
                {"label": "진화대", "n": 81},
                {"label": "공무원", "n": 13},
                {"label": "소방", "n": 57},
            ],
            "orgs": ["의성군 산림녹지과", "의성소방서"],
        },
        "vehicle": {
            "total": 26,
            "by": [
                {"label": "지휘·진화차", "n": 10},
                {"label": "소방차", "n": 15},
                {"label": "기타", "n": 1},
            ],
        },
        "unknown": [],
    },
    "weather": {
        "now": {"dir": "서", "ms": 3.4, "temp_c": 22.9, "rh_pct": 22},
        "mean_0_5_ms": 5.5,
        "hourly": [
            {"h": 0, "at": "12:50", "dir": "서", "ms": 3.4},
            {"h": 3, "at": "15:50", "dir": "서북서", "ms": 7.2},
        ],
        "max": {"ms": 7.2, "dir": "서북서", "hours": ["15:50"]},
        "unknown": [{"field": "헬기 운항 가능 여부", "reason": "운항 기준 없음"}],
    },
    "spread": {
        "dir": "남서",
        "km": 0.1,
        "next_villages": [
            {"name": "신안리", "emd": "안평면", "zone": "p5", "from": "15:50", "until": "16:50"}
        ],
    },
    "agencies": {
        "by_sigungu": [{"sigungu": "의성군", "fire": "의성소방서", "police": "의성경찰서"}],
        "emergency_medical": [{"name": "제남병원", "sigungu": "의성군", "km": 7.9}],
        "unknown": [],
    },
}

P72 = {
    "chunk_id": "SM-p072-s01",
    "label": "p.72",
    "doc_name": "「산불 재난」 위기관리 표준매뉴얼",
    "section": "산불 대응단계",
    "page": "72",
    "origin": "판정 근거",
    "text": "피해면적이 100 ha 이상으로 예상되는 대형산불은 시 · 도지사가 통합지휘한다. 대응단계는 산림청장이 발령한다.",
}
P75 = {
    "chunk_id": "SM-p075-s02",
    "label": "p.75",
    "doc_name": "「산불 재난」 위기관리 표준매뉴얼",
    "section": "주민 대피",
    "page": "75",
    "origin": "검색",
    "text": "잠재적 위험구역에는 일몰 전에 사전 대피를 실시한다. 노약자를 먼저 대피시킨다.",
}

ITEMS = [
    {
        "item_code": "S1",
        "name": "대응단계 판단·격상 검토·지휘권 인계",
        "state": "ok",
        "categories": ["협의"],
        "sections": [
            {
                "title": "",
                "blocks": [
                    {
                        "type": "paragraph",
                        "category": "협의",
                        "body": "피해면적이 14:50에 119.7 ha로 확산대응 2단계 기준 100 ha를 넘을 수 있습니다. 확산대응 2단계 발령을 산림청장과 협의하십시오.",
                        "cite": "[p.72]",
                        "evidence": ["SM-p072-s01"],
                    },
                ],
            }
        ],
        "evidence": [P72],
        "text": "피해면적이 14:50에 119.7 ha로 확산대응 2단계 기준 100 ha를 넘을 수 있습니다. 확산대응 2단계 발령을 산림청장과 협의하십시오.",
        "detail": {
            "id": "S1",
            "name": "대응단계 판단·격상 검토·지휘권 인계",
            "finding": "현재 기준 1단계 · P5 기준 2단계 → 5시간 안에 2단계 기준 도달 예상 · 지휘권자 의성군수 → 경상북도지사",
            "withheld": [{"field": "진화예상시간", "reason": "MVP에서 제외"}],
        },
    },
    {
        "item_code": "S3",
        "name": "진화자원 투입·동원 범위",
        "state": "ok",
        "categories": ["즉시"],
        "sections": [
            {
                "title": "",
                "blocks": [
                    {
                        "type": "paragraph",
                        "category": "즉시",
                        "body": "현재 투입된 헬기 23대를 유지하십시오.",
                        "cite": "[고시 별표]",
                        "evidence": ["NOTI-MOBL-t0-p1"],
                    },
                ],
            }
        ],
        "evidence": [],
        "text": "현재 투입된 헬기 23대를 유지하십시오.",
        "detail": {
            "id": "S3",
            "finding": "투입 헬기 23 · 인력 151 · 차량 26",
            "withheld": [{"field": "추가 투입 헬기 대수", "reason": "산정 기준 없음"}],
        },
    },
    {
        "item_code": "E2",
        "name": "대피 순서·시점",
        "state": "ok",
        "categories": ["즉시"],
        "sections": [
            {
                "title": "",
                "blocks": [
                    {
                        "type": "paragraph",
                        "category": "즉시",
                        "body": "박곡리(280명) 주민에게 일몰 전(18:38)까지 사전 대피를 완료하도록 명령하십시오.",
                        "cite": "[p.75]",
                        "evidence": ["SM-p075-s02"],
                    },
                    {
                        "type": "list",
                        "rows": [{"text": "지금(이미 닿음): **괴산리**(147명), **신월리**(128명)"}],
                    },
                    {
                        "type": "list",
                        "rows": [{"text": "19:50 ~ 20:50 닿을 수 있음(밤): **박곡리**(280명)"}],
                    },
                ],
            }
        ],
        "evidence": [P75],
        "text": "박곡리(280명) 주민에게 일몰 전(18:38)까지 사전 대피를 완료하도록 명령하십시오.",
        "detail": {
            "id": "E2",
            "finding": "위험구역 3곳 456명 / 잠재 위험구역 1곳 280명: 박곡리(밤에 닿음) · 일몰 18:38",
            "withheld": [],
        },
    },
]

DIRECTORY = [
    {"id": 101, "name": "괴산리", "emd": "안평면", "sigungu": "의성군", "home": True},
    {"id": 102, "name": "신월리", "emd": "안평면", "sigungu": "의성군", "home": True},
    {"id": 103, "name": "신안리", "emd": "안평면", "sigungu": "의성군", "home": True},
    {"id": 104, "name": "박곡리", "emd": "안평면", "sigungu": "의성군", "home": True},
    {"id": 105, "name": "하화리", "emd": "안평면", "sigungu": "의성군", "home": True},
    {"id": 106, "name": "하화리", "emd": "옥산면", "sigungu": "의성군", "home": True},
    {"id": 107, "name": "도옥리", "emd": "안평면", "sigungu": "의성군", "home": True},
    {"id": 201, "name": "괴산리", "emd": "임하면", "sigungu": "안동시", "home": False},
]

SITUATION = {
    "villages": {"102": {"alert_at": "12:40", "reported_at": "12:50"}},
    "deployed": {
        "helicopter": {"total": 23, "forest": 5, "local": 16, "fire": 2},
        "crew": {"total": 151, "special_crew": 81, "forest_officials": 13, "fire": 57},
        "vehicle": {"total": 26, "forest_vehicle": 10, "fire_truck": 15, "other": 1},
        "reported_at": "12:50",
    },
}


def make_context(**over) -> Context:
    ctx = Context(
        incident={
            "id": 1,
            "name": "의성군 안평면 산불 (2025-03-22)",
            "sigungu": "의성군",
            "emd": "안평면",
            "status": "진행 중",
            "started_at": "2025-03-22 11:24",
            "reported_at": "2025-03-22 11:25",
            "current_stage": "확산대응 1단계",
        },
        reader="의성군수",
        situation=copy.deepcopy(SITUATION),
        proposal={
            "id": 7,
            "version": "v1.1",
            "trigger": "예측 갱신",
            "created_at": "2025-03-22T12:55:00+09:00",
            "base_at": "2025-03-22 12:50",
        },
        bundles=copy.deepcopy(BUNDLES),
        items=copy.deepcopy(ITEMS),
        villages=copy.deepcopy(DIRECTORY),
        revision="rev-0",
    )
    for k, v in over.items():
        setattr(ctx, k, v)
    return ctx
