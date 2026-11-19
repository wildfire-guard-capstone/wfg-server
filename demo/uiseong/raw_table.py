# ruff: noqa: E501
"""원자료 표(평가 기준선 C1 · C2 전용, 11/19 재설계 6장 표 9 · 10 · 결정 D5 · D6 · D7).

C1 · C2는 규칙층 없이 원자료로 판단(범위 포함 · 거리 · 도달 시각 · 수치 · 단계)해야 하므로, 규칙 엔진이 읽는 것과
같은 범위의 자료(demo/uiseong/data.py · step2_rules.make_inputs)를 글로 펴서 프롬프트에 넣는다. 제품(C3)은 쓰지 않는다.

    text = build(md, rd, spread, inputs_summary, prev_stage, prev_alerts)   # 회차마다 한 번(12항목 공통)

- 넣는 것: 확산 P0 ~ P8(30 m 단순화 좌표) · 발화점 · 기준 시각 · 화선 보고 · 기상(0 ~ 8시간) · 마을(이름 · 읍면 · 인구 ·
  주택 수 · 중심 좌표, 발화점 10 km 안은 50 m 단순화 경계) · 취약시설 · 대피소 후보 · 국가유산 · 변전소 · 발전소 ·
  송전선(30 m 단순화) · 큰 도로(10 km 안, 30 m 단순화) · 소방 · 경찰 · 응급 의료 · 헬기 기지(대수 · 기종) · 현장 입력 ·
  투입 자원 · 직전 제안서(단계 · 재난문자 지역) · 읽는 사람 · 이웃 시군 · 산림 부서 · 일출 · 일몰 · 있는 자료 목록.
- 주지 않는 것: 미리 계산된 거리 칸(헬기 기지 km, 의료기관 dist_km, 소방 inside, 의성 안 여부), 2025 실제 피해 범위
  (map 19층 — 정답 누설), 규칙층이 계산한 값 전부(면적 · 포함 · 도달 시각 · 단계 · 지휘권자 …), 송전철탑, 소로,
  상황 보고의 화선 면적 메모(규칙 엔진도 읽지 않음).
- 좌표는 WGS84 경도 · 위도, 소수 5자리(약 1 m). centers_only=True면 마을 경계를 빼고 중심 좌표만 준다(D5 대안).
- coords="local"(11-19 사용자 결정, 64K에 맞추기): 발화점을 (0, 0)으로 한 평면 좌표 「동쪽 m 북쪽 m」(EPSG:5179,
  10 m 단위). 좌표 글이 짧아 원자료 표 토큰이 약 42% 줄고(회차 4: 85,078 → 49,491), 모양 · 단순화는 그대로다.
"""

from datetime import timedelta
from typing import Any

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import linemerge, unary_union

from demo.uiseong.data import FOREST_DEPT, HOME, MapData, lonlat, to4326
from rules.core.stage import LABEL
from rules.core.sun import sun_times

RADIUS_M = 10_000  # D6: 마을 경계 · 큰 도로는 발화점 10 km 안만
RANGE_BUF_M = 2_000  # 대안(near_mode="range"): 이번 회차 P8 범위 둘레 2 km 안
TOL_SPREAD = 30  # m, 확산 범위 · 송전선 · 도로 단순화
TOL_VILLAGE = 50  # m, 마을 경계 단순화
DIRS = "북 북북동 북동 동북동 동 동남동 남동 남남동 남 남남서 남서 서남서 서 서북서 북서 북북서".split()


def compass(deg: float) -> str:
    """풍향(불어오는 쪽, 도) → 16방위 이름."""
    return DIRS[int(((deg % 360) + 11.25) // 22.5) % 16]


def _xy(x: float, y: float) -> str:
    lon, lat = lonlat(Point(x, y))
    return f"{lon:.5f} {lat:.5f}"


LOCAL_STEP_M = 10  # coords="local"의 반올림 단위(m)


def local_xy(origin):
    """발화점 기준 평면 좌표(동 · 북, m, LOCAL_STEP_M 단위)를 쓰는 함수."""

    def f(x: float, y: float) -> str:
        e = round((x - origin.x) / LOCAL_STEP_M) * LOCAL_STEP_M
        n = round((y - origin.y) / LOCAL_STEP_M) * LOCAL_STEP_M
        return f"{e:.0f} {n:.0f}"

    return f


def pt(g, xy=_xy) -> str:
    c = g if g.geom_type == "Point" else g.representative_point()
    return xy(c.x, c.y)


def _ring(coords, xy=_xy) -> str:
    return "; ".join(xy(x, y) for x, y, *_ in coords)


def poly_text(g, tol: float, xy=_xy) -> str:
    """면(EPSG:5179) → 단순화한 꼭짓점 글. 여러 조각 · 구멍은 「| 조각」 「(구멍 …)」으로."""
    g = g.simplify(tol, preserve_topology=True)
    parts = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
    out = []
    for p in parts:
        if not isinstance(p, Polygon) or p.is_empty:
            continue
        s = _ring(p.exterior.coords, xy)
        holes = [f"(구멍 {_ring(h.coords, xy)})" for h in p.interiors if Polygon(h).area > 1e4]
        out.append(" ".join([s, *holes]))
    return " | ".join(out)


def line_text(g, tol: float, xy=_xy) -> str:
    g = g.simplify(tol, preserve_topology=False)
    parts = list(g.geoms) if isinstance(g, MultiLineString) else [g]
    return " | ".join(
        _ring(p.coords, xy) for p in parts if isinstance(p, LineString) and not p.is_empty
    )


def _row(*cells) -> str:
    return " | ".join("" if c is None else str(c) for c in cells)


def _props(md: MapData, prefix: str) -> list[dict]:
    return [p for p, _ in md._layer(prefix)]


def build(
    md: MapData,
    rd,
    spread,
    inputs: dict[str, Any],
    prev_stage: int | None = None,
    prev_alerts: tuple = (),
    centers_only: bool = False,
    near_mode: str = "ignition",
    coords: str = "wgs84",
) -> str:
    """회차 하나의 원자료 표 글. inputs = step2_rules.make_inputs의 요약(바람 · 현장 입력 · 투입 자원).

    near_mode = 마을 경계 · 큰 도로를 넣을 구역. "ignition" = 발화점 10 km 안(D6 글 그대로),
    "range" = 이번 회차 P0 ~ P8 범위 둘레 2 km 안(11-19 밤 대안: 확산 모형의 추가 발화 조각이 발화점에서
    약 13.6 km라 D6 구역 밖에 불이 닿는 마을 · 도로가 생김 — 아침 확인).
    coords = 좌표 꼴. "wgs84" = 경도 · 위도 소수 5자리(표 9 글 그대로), "local" = 발화점 기준 동 · 북 미터."""
    base = rd.base_at
    ig = md.ignition
    if near_mode == "range":
        near = unary_union(spread.ranges).buffer(RANGE_BUF_M)
        zone = "이번 회차 확산 범위 둘레 2 km 안"
    elif near_mode == "ignition":
        near = ig.buffer(RADIUS_M)
        zone = "발화점 10 km 안"
    else:
        raise ValueError(f"near_mode는 ignition 또는 range: {near_mode}")
    if coords == "wgs84":
        xy = _xy
        coord_note = "좌표: WGS84 「경도 위도」, 소수 5자리."
    elif coords == "local":
        xy = local_xy(ig)
        coord_note = (
            f"좌표: 발화점을 0 0으로 한 평면 좌표 「동쪽 거리 북쪽 거리」(단위 m, {LOCAL_STEP_M} m 단위로 반올림). "
            "동쪽 · 북쪽이 +, 서쪽 · 남쪽이 −입니다."
        )
    else:
        raise ValueError(f"coords는 wgs84 또는 local: {coords}")

    def P(g) -> str:  # noqa: N802 — 이 표의 좌표 꼴로 점 하나
        return pt(g, xy)

    L: list[str] = [
        "[원자료 표 — 이번 회차의 원자료입니다. 규칙으로 미리 계산한 값(면적 · 포함 여부 · 거리 · 도달 시각 · 단계 · 지휘권자)은 없습니다. 필요한 판단은 이 자료로 하십시오]",
        coord_note
        + " 면은 꼭짓점을 「;」로 이어 첫 점으로 닫고, 여러 조각은 「|」로 나눕니다. 선도 꼭짓점을 「;」로 잇습니다.",
    ]
    # ── 있는 자료
    L.append(
        "\n## 있는 자료\n확산 예측 P0 ~ P8, 발화점 · 기준 시각 · 화선 보고, 기상(지금 ~ 8시간 뒤 바람, 지금 기온 · 습도), 일출 · 일몰, "
        "마을(리) 인구 · 주택 수 · 위치, 취약시설, 대피소 후보, 국가유산, 변전소 · 발전소, 송전선, 큰 도로, 소방 · 경찰 · 응급 의료기관 · 진화헬기 기지, "
        "현장 입력(마을별 대피 문자 · 명령 · 완료 보고), 투입 자원, 직전 제안서(단계 · 재난문자 지역), 읽는 사람 · 이웃 시군 · 산림 부서"
    )
    # ── 시각 · 발화점 · 화선
    L.append("\n## 기준 시각 · 발화 · 화선 보고")
    L.append(f"- 기준 시각(지금): {base:%Y-%m-%d %H:%M}")
    wgs = "" if coords == "wgs84" else f"(WGS84 {pt(ig)})"
    L.append(f"- 발화: {md.perimeters['ignition'].get('at', '')} · 발화점 {P(ig)}{wgs}")
    L.append(f"- 화선 보고: {rd.report}({rd.at}, 상황도) — 아래 P0이 이 화선입니다")
    # ── 확산
    L.append(
        "\n## 확산 예측 P0 ~ P8(P0 = 기준 시각 화선, Pn = 기준 시각부터 n시간 뒤까지 불이 닿을 수 있는 범위, 30 m 단순화)"
    )
    for h, g in enumerate(spread.ranges):
        L.append(f"P{h}(~{base + timedelta(hours=h):%H:%M}): {poly_text(g, TOL_SPREAD, xy)}")
    # ── 기상 · 해
    L.append("\n## 기상(0시간 = 상황 보고, 1 ~ 8시간 = 기상 예보)")
    L.append(_row("시각", "풍속 m/s", "풍향(불어오는 쪽)"))
    ws, wd = inputs.get("wind_ms", {}), inputs.get("wind_deg", {})
    for h in sorted(ws, key=int):
        deg = float(wd.get(h, wd.get(str(h), 0)))
        L.append(
            _row(f"{base + timedelta(hours=int(h)):%H:%M}", ws[h], f"{deg:.0f}°({compass(deg)})")
        )
    L.append(f"지금 기온 {inputs.get('temp_c')} °C · 습도 {inputs.get('rh_pct')} %")
    lon, lat = lonlat(ig)
    today = sun_times(base.date(), lat, lon)
    nxt = sun_times((base + timedelta(days=1)).date(), lat, lon)
    L.append(
        f"일출 · 일몰(발화점, 한국 시간): 오늘 일출 {today.sunrise:%H:%M} · 일몰 {today.sunset:%H:%M}, 다음 날 일출 {nxt.sunrise:%H:%M}"
    )
    # ── 현장 입력 · 자원 · 직전 제안서 · 읽는 사람
    L.append("\n## 현장 입력(마을별, 보고된 것만)")
    fields = inputs.get("field") or {}
    if not fields:
        L.append("- 없음")
    for name, f in fields.items():
        bits = []
        if f.get("alert_at"):
            bits.append(f"마지막 대피 문자 {f['alert_at']}")
        bits.append("대피명령 내림" if f.get("evac_order") else "대피명령 없음")
        bits.append("대피 완료 보고 있음" if f.get("evacuated") else "대피 완료 보고 없음")
        if f.get("pre_evac_done"):
            bits.append("사전대피 완료")
        if f.get("reported_at"):
            bits.append(f"보고 시각 {f['reported_at']}")
        L.append(f"- {name}: " + ", ".join(bits))
    d = inputs.get("deployed") or {}
    L.append(f"\n## 투입 자원({d.get('reported_at') or rd.at} 상황 보고)")
    for key, label, unit in (
        ("helicopter", "헬기", "대"),
        ("crew", "인력", "명"),
        ("vehicle", "차량", "대"),
    ):
        row = d.get(key) or {}
        total = sum(row.values())
        L.append(f"- {label} {total}{unit}: " + ", ".join(f"{k} {v}" for k, v in row.items()))
    L.append("\n## 직전 제안서(직전 회차)")
    if prev_stage is None and not prev_alerts:
        L.append("- 없음(이번이 첫 제안서)")
    else:
        if prev_stage is not None:
            L.append(f"- 직전 단계: {LABEL.get(prev_stage, prev_stage)}")
        if prev_alerts:
            L.append(
                "- 직전 재난문자 지역: "
                + ", ".join(f"{sg} {emd}({lv.capitalize()})" for sg, emd, lv in prev_alerts)
            )
    L.append("\n## 읽는 사람 · 기관")
    L.append(f"- 읽는 사람: {rd.reader} · 관할 시군: {HOME} · 시 · 도: 경상북도")
    L.append(f"- 이웃 시군: {', '.join(md.neighbors.get(HOME, []))}")
    L.append(f"- 산림 부서: {', '.join(FOREST_DEPT.values())}")
    # ── 마을
    houses = {int(p["code"]): p.get("houses") for p in _props(md, "03")}
    # 표 9: 마을(리) 182곳 = 의성군 법정리. 규칙 엔진이 쓰는 의성 밖 「○○면 전체」(읍면 대용)는 표 9에 없어 뺀다
    vs = [v for v in md.villages if v.id not in md.township_ids]
    L.append(
        f"\n## 마을 {len(vs)}곳(의성군 법정리) — 이름 | 읍면 | 시군 | 인구 | 주택 수 | 중심"
        + ("" if centers_only else f" | 경계({zone} 마을만, 50 m 단순화)")
    )
    for v in vs:
        cells = [
            v.name,
            v.emd,
            v.sigungu,
            v.population,
            houses.get(v.id, "") or "",
            P(v.geom.centroid),
        ]
        if not centers_only and v.geom.intersects(near):
            cells.append(poly_text(v.geom, TOL_VILLAGE, xy))
        L.append(_row(*cells))
    # ── 시설
    vul = _props(md, "07")
    L.append(f"\n## 취약시설 {len(vul)}곳 — 이름 | 종류 | 정원 | 현원 | 위치")
    for p, place in zip(vul, md.vulnerable, strict=True):
        L.append(_row(p["name"], p.get("group"), p.get("cap"), p.get("now"), P(place.geom)))
    L.append(
        f"\n## 대피소 후보 {len(md.shelters)}곳(이재민 임시주거시설) — 이름 | 종류 | 수용 인원 | 위치"
    )
    for s in md.shelters:
        L.append(_row(s.name, s.kind, s.capacity if s.capacity is not None else "모름", P(s.geom)))
    L.append(f"\n## 국가유산 {len(md.heritage)}곳 — 이름 | 종류 | 위치")
    L += [_row(h.name, h.kind, P(h.geom)) for h in md.heritage]
    L.append(f"\n## 변전소 · 발전소 {len(md.substations)}곳 — 이름 | 종류 | 위치")
    L += [_row(s.name, s.kind, P(s.geom)) for s in md.substations]
    L.append(f"\n## 송전선 {len(md.power_lines)}개 — 전압 | 선(30 m 단순화)")
    L += [_row(f"{p.voltage_kv} kV", line_text(p.geom, TOL_SPREAD, xy)) for p in md.power_lines]
    # ── 큰 도로(10 km 안, 이름 · 등급별로 이어 붙임)
    groups: dict[tuple, list] = {}
    for r in md.roads:
        if r.geom.intersects(near):
            groups.setdefault((r.name or "(이름 없음)", r.grade), []).append(
                r.geom.intersection(near)
            )
    L.append(f"\n## 큰 도로({zone} {len(groups)}개 노선 · 등급) — 이름 | 등급 | 선(30 m 단순화)")
    for (name, grade), gs in sorted(groups.items()):
        merged = linemerge(unary_union(gs)) if len(gs) > 1 else gs[0]
        txt = line_text(merged, TOL_SPREAD, xy)
        if txt:
            L.append(_row(name, grade, txt))
    # ── 기관
    L.append(f"\n## 소방 {len(md.fire_stations)}곳 — 이름 | 종류 | 시군 | 위치")
    L += [_row(f.name, f.kind, f.sigungu, P(f.geom)) for f in md.fire_stations]
    L.append(f"\n## 경찰 {len(md.police)}곳 — 이름 | 종류 | 시군 | 위치")
    L += [_row(f.name, f.kind, f.sigungu, P(f.geom)) for f in md.police]
    hos = _props(md, "12")
    L.append(f"\n## 응급 의료기관 {len(hos)}곳 — 이름 | 종류 | 시군 | 위치")
    for p, h in zip(hos, md.hospitals, strict=True):
        L.append(_row(p["name"], p.get("kind"), h.sigungu, P(h.geom)))
    bp = _props(md, "10")
    L.append(f"\n## 진화헬기 기지 {len(bp)}곳 — 이름 | 운영 | 시군 | 헬기 대수 | 기종 | 위치")
    for p, b in zip(bp, md.bases, strict=True):
        L.append(_row(p["name"], b.kind, b.sigungu, p.get("n"), p.get("models"), P(b.geom)))
    return "\n".join(L)


def to_geojson_check(text: str) -> dict[str, int]:
    """크기 가늠(시험 · 기록용): 글자 수와 줄 수."""
    return {"chars": len(text), "lines": text.count("\n") + 1}


__all__ = ["build", "compass", "to4326"]
