# ruff: noqa: E501
"""예측에 실제로 쓴 기상 → EC2 서버의 predictions.weather 모양(app.weather.base, 9시간).

GPU 서버의 ELMFIRE 결과(round_k/01_spread)를 EC2의 file 예측(app.predict.file)으로 넘길 때
weather.json으로 같이 넘긴다. weather 파일이 있으면 그 기상이 예측 · 규칙 엔진 · 제안서의 기상이 된다
(app.predict.base: 「ELMFIRE가 다른 기상으로 계산했다면 결과 weather에는 실제로 쓴 그 기상」).

    w = weather_used(deck_dir, md.ignition, rd.base_at, rd.band1, input_dir / "risk/weather_hourly.csv")

- 시각: 회차 시각을 내림한 정시부터 1시간 간격 9개(app.weather.base.hour_slots와 같음, 한국 시간)
- 풍향 · 풍속: ELMFIRE가 읽은 바람 래스터(덱 namelist WS_FILENAME · WD_FILENAME)의 그 정시 밴드를
  발화점 칸에서 그대로 읽는다(EC2 기상 기준점 = 발화점과 같게). mph → m/s.
  11/9 최종 덱은 ws_gust(시간 안 가장 센 1분 풍속)라 시간평균 풍속보다 크다
- 기온 · 습도 · 강수: ASOS 의성(278) 시간자료(input/risk/weather_hourly.csv). ELMFIRE는 기온 · 습도를
  직접 받지 않고, 관측소 22곳(이 지점 포함)의 기온 · 습도로 계산한 연료수분 래스터를 읽는다
"""

import csv
import math
import re
from datetime import datetime
from pathlib import Path

from app.weather.base import KST, Weather, check_weather, hour, hour_slots
from demo.uiseong.data import lonlat

MPH = 0.44704  # mph → m/s
SOURCE = "ELMFIRE_DECK"
ASOS_STN = "278"  # 의성


def namelist_file(namelist: str, key: str) -> str | None:
    """elmfire.data의 KEY = 'name' 값(뒤 주석은 무시)."""
    m = re.search(rf"^\s*{key}\s*=\s*'([^']+)'", namelist, re.M)
    return m.group(1) if m else None


def read_asos(path: Path) -> dict[datetime, dict[str, float]]:
    """weather_hourly.csv(time · temp_c · precip_mm · rh_pct …, 한국 시간) → 정시 → 값."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            at = datetime.fromisoformat(row["time"]).replace(tzinfo=KST)
            out[at] = {
                "temp": float(row["temp_c"]),
                "humidity": float(row["rh_pct"]),
                "precip": float(row["precip_mm"] or 0.0),
            }
    return out


def build_weather(
    slots: list[datetime],
    winds: list[tuple[float, float]],
    asos: dict[datetime, dict[str, float]],
    site: tuple[float, float],
    wind_from: str,
) -> Weather:
    """정시 9개 · (풍속 m/s, 풍향 °) 9개 · ASOS 행 → predictions.weather. site = (위도, 경도)."""
    if len(winds) != len(slots):
        raise ValueError(f"바람 {len(winds)}개 · 시각 {len(slots)}개가 맞지 않습니다")
    hours = []
    for t, (ws, wd) in zip(slots, winds, strict=True):
        if t not in asos:
            raise ValueError(f"ASOS {ASOS_STN} 시간자료에 {t:%Y-%m-%d %H:%M}이 없습니다")
        a = asos[t]
        hours.append(hour(t, "관측", wd % 360, ws, a["temp"], a["humidity"], a["precip"]))
    return {
        "source": SOURCE,
        "issued_at": None,
        "location": {"lat": round(site[0], 6), "lon": round(site[1], 6), "stn": ASOS_STN},
        "hours": hours,
        "note": (
            f"풍향 · 풍속 = ELMFIRE 입력 {wind_from}(발화점 칸, 그 정시 밴드) · "
            f"기온 · 습도 · 강수 = ASOS 의성({ASOS_STN})"
        ),
    }


def deck_winds(
    deck_dir: Path, xy: tuple[float, float], slots: list[datetime], band1: datetime
) -> tuple[list[tuple[float, float]], str]:
    """덱 바람 래스터를 한 점(EPSG:5179)에서 정시마다 읽는다 → [(m/s, °)], 출처 설명.
    밴드 1 = band1(정시), 1시간 간격(DT_METEOROLOGY 3600)."""
    import rasterio

    namelist = (deck_dir / "elmfire.data").read_text(encoding="utf-8")
    ws_name = namelist_file(namelist, "WS_FILENAME") or "ws"
    wd_name = namelist_file(namelist, "WD_FILENAME") or "wd"

    def series(name: str) -> list[float]:
        with rasterio.open(deck_dir / "inputs" / f"{name}.tif") as r:
            row, col = r.index(*xy)
            if not (0 <= row < r.height and 0 <= col < r.width):
                raise ValueError(f"{name}.tif 밖의 점입니다: {xy}")
            vals = r.read()[:, row, col].astype(float).tolist()
        out = []
        for t in slots:
            i = round((t.replace(tzinfo=None) - band1).total_seconds() / 3600)
            if not 0 <= i < len(vals):
                raise ValueError(
                    f"{name}.tif에 {t:%m-%d %H:%M} 밴드가 없습니다(밴드 {len(vals)}개)"
                )
            if not math.isfinite(vals[i]):
                raise ValueError(f"{name}.tif {t:%m-%d %H:%M} 값이 비어 있습니다")
            out.append(vals[i])
        return out

    ws, wd = series(ws_name), series(wd_name)
    return [(round(s * MPH, 1), round(d, 1)) for s, d in zip(ws, wd, strict=True)], (
        f"{ws_name} · {wd_name}"
    )


def weather_used(
    deck_dir: Path, ignition, base_at: datetime, band1: datetime, asos_csv: Path
) -> Weather:
    """회차 기준 시각(base_at, 한국 시간 naive)의 9시간 기상 — 검사(check_weather)까지 통과한 값."""
    slots = hour_slots(base_at.replace(tzinfo=KST))
    winds, wind_from = deck_winds(deck_dir, (ignition.x, ignition.y), slots, band1)
    lon, lat = lonlat(ignition)
    return check_weather(build_weather(slots, winds, read_asos(asos_csv), (lat, lon), wind_from))
