# ruff: noqa: E501
"""요청 표 틀(S6 · E6): 엔진 묶음의 대상과 관서 이름을 매뉴얼의 기관별 임무로 짝짓는다.

엔진이 아니라 항목(조립기) 쪽이다. 「어느 기관에 무엇을」은 매뉴얼 문장을 옮긴 고정 표이고
(11/7 점검), 엔진은 대상과 관서 이름(사실)만 낸다.
행 = 관련 기관 · 세부 대상 · 대상 · 시각 · 항목. 같은 대상 종류면 늘 같은 행 모양이 나온다.

11/19 재설계(결정 D3 · D1): 「요청 사항」 칸(매뉴얼 문장을 옮긴 할 일)과 「근거」 칸(매뉴얼 쪽 지정)을 뺐다.
무엇을 요청할지는 제안 문단이 근거 발췌로 쓴다. 표에는 기관 · 대상 · 시각만 남긴다. 거주자 이송을 맡을 기관(보건 행)도
담당 지정이라 뺐다.
"""

from typing import Any

HEAD = ["관련 기관", "세부 대상(기관명)", "대상", "시각", "항목"]
HELI_GROUP = {"산림청 헬기": "산림 헬기", "소방 헬기": "소방 헬기", "지자체 임차헬기": "임차 헬기"}
CENTRAL_ROOM = "중앙재난안전상황실"  # 재난방송 송출 요청 곳(기관 이름은 표 틀에 둔다, 11/7)


def _row(group: str, agency: str, target: str, when: str, item: str) -> dict[str, str]:
    return {"group": group, "agency": agency, "target": target, "when": when, "item": item}


def _home(b: dict[str, Any]) -> dict[str, Any]:
    ag = b["agencies"]
    return next((r for r in ag["by_sigungu"] if r["sigungu"] == ag["home"]), {})


def _when(r: dict[str, Any]) -> str:
    return "지금" if r["zone"] == "burning" else f"{r['from']} ~ {r['until']}"


def _join(names) -> str:
    return ", ".join(dict.fromkeys(n for n in names if n))


def _spare(r: dict[str, Any]) -> int | None:
    """기지에 남은(대기) 대수: 헬기 기지 = 헬기, 지상 기지 = 차량. None = 모름."""
    return r["available"]["helicopters" if r["kind"].endswith("헬기") else "vehicles"]


def s_rows(b: dict[str, Any]) -> list[dict[str, str]]:
    """진화 쪽 요청(S2 · S3 대상).

    1. 소방(p.79): 5시간 안 주택 · 문화유산 → 관할 소방서
    2. 전력(p.95): 5시간 안에 걸린 송전선 → 한국전력공사
    3. 경찰(p.79) · 국가유산(p.79 · 96): 5시간 안 사찰 · 국가유산 → 가까운 경찰서 · 국가유산청
    4. 헬기 · 지상 응원(고시 별표): 지금 범위 안 응원 기지를 종류별로. 대기가 0인 기지(다 나감)는
       빼고, 모름(null)은 남긴다
    """
    rows = []
    pr, h, home = b["protected"], b["houses"], _home(b)
    heritage = [r for r in pr["temples"] + pr["heritage"]["items"] if r["zone"] != "p8"]
    houses = [f"{r['village']} 주택 최소 {r['houses']}동" for r in h["within_p5"]["by_village"]]
    if houses or heritage:  # 1
        target = ", ".join(houses + [r["name"] for r in heritage])
        rows.append(_row("소방", home.get("fire") or "관할 소방서", target, "5시간 안", "S2"))
    for line in pr["power_lines"]:  # 2
        if line["km_p5"]:
            target = f"{line['name']}({line['km_p5']} km)"
            rows.append(_row("전력", "한국전력공사", target, "5시간 안", "S2"))
    if heritage:  # 3
        names = _join(r["name"] for r in heritage)
        police = _join((r["police"] or {}).get("name") for r in heritage) or "관할 경찰서"
        rows.append(_row("경찰", police, names, "5시간 안", "S2"))
        rows.append(_row("국가유산", "국가유산청", names, "5시간 안", "S2"))
    mob = b["mobilization"]  # 4
    air = mob["now"]["air"]
    reach = "전국" if air["kind"] == "national" else f"발화지 반경 {air['km']} km 안"
    support = [
        r
        for r in mob["bases"]
        if r["first_scope"] == "now" and r["jurisdiction"] == "응원" and _spare(r) != 0
    ]
    targets = {"산림청 헬기": f"진화헬기({reach})", "소방 헬기": "소방헬기"}
    targets["지자체 임차헬기"] = "임차헬기"
    for kind, group in HELI_GROUP.items():
        bases = [r for r in support if r["kind"] == kind]
        if bases:  # 임차헬기는 빌린 시군이 요청 대상
            agency = _join(r["sigungu"] if kind == "지자체 임차헬기" else r["name"] for r in bases)
            rows.append(_row(group, agency, targets[kind], "지금", "S3"))
    ground = [r for r in support if r["kind"].startswith("지상")]
    if ground:
        share = mob["now"]["ground"]["extra"]["share"] or "100%"
        rows.append(
            _row(
                "지상 응원",
                _join(r["name"] for r in ground),
                f"진화인력 · 장비({share})",
                "지금",
                "S3",
            )
        )
    return rows


def e_rows(b: dict[str, Any]) -> list[dict[str, str]]:
    """대피 쪽 요청(E2 · E3 · E4 · E5 대상).

    1. (11/19 뺌) 보건: 취약시설 거주자 이송을 맡을 기관은 담당 지정이라 규칙층에서 뺐다(재설계 D3).
    2. 의료(p.89): 5시간 안 취약시설이 있고 응급의료기관이 있으면
    3. 교통(p.79 · 83 · 95): 큰 도로 구간마다 → 가까운 경찰서 · 한국도로공사(p.83 · 95 기관별 임무
       「고속도로, 국도, 지방도 등 교통통제」)
    4. 치안(p.96): 위험구역 마을 → 관할 경찰서
    5. 재난방송(p.78) · 마을 조직(p.76): Go · Set 읍면
    """
    rows = []
    vul, home = b["vulnerable"]["in_range"], _home(b)
    if vul:
        now = [r for r in vul if r["zone"] != "p8"]
        medical = b["agencies"]["emergency_medical"]
        if now and medical:  # 2
            groups: dict[str, list[str]] = {}
            for r in now:
                groups.setdefault(_when(r), []).append(r["name"])
            rows.append(
                _row(
                    "의료",
                    _join(m["name"] for m in medical),
                    "; ".join(", ".join(names) for names in groups.values()),
                    "; ".join(groups),
                    "E3",
                )
            )
    for r in b["roads"]["sections"]:  # 3
        agency = _join([(r["police"] or {}).get("name") or "관할 경찰서", "한국도로공사"])
        rows.append(_row("교통", agency, f"{r['road']} {r['section']}", _when(r), "E4"))
    n = b["villages"]["totals"]["within_p5"]["count"]
    if n:  # 4
        rows.append(
            _row(
                "치안",
                home.get("police") or "관할 경찰서",
                f"위험구역 {n}개 리",
                "",
                "E2",
            )
        )
    areas = b["alerts"]["areas"]
    if areas:  # 5
        sido = b["agencies"]["sido"]
        stages = []
        for level, label in (("go", "Go"), ("set", "Set")):
            emds = [a["emd"] for a in areas if a["level"] == level]
            if emds:
                stages.append(f"{label}: {'·'.join(emds)}")
        rows.append(
            _row(
                "재난방송",
                f"{CENTRAL_ROOM} 또는 {sido} 재난안전상황실",
                ", ".join(stages),
                "",
                "E5",
            )
        )
        emds = "·".join(dict.fromkeys(a["emd"] for a in areas))
        rows.append(_row("마을 조직", f"{emds} 이장, 마을순찰대", f"{emds} 주민", "", "E2"))
    return rows
