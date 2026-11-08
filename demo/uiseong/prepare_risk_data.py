# ruff: noqa: E501
"""의성 데모 위험도 입력 준비(한 번, 로컬): 과거 기상 · 취약계층 비율 → <input>/risk/.

    python -m demo.uiseong.prepare_risk_data --input <demo-uiseong/input> \
        --asos <asos_278_20250312_20250322.csv> --sgis-zip <SGIS 행정구역 통계 및 경계 zip>

만드는 것(공개 저장소에 넣지 않는 입력 폴더):
  risk/weather_hourly.csv   2025-03-12 ~ 22 의성 시간별 기상
      기온 · 강수 · 풍속 · 풍향 · 습도 = 기상청 ASOS 의성(278) 관측(feature/uiseong-elmfire-example 브랜치
      spread/examples/uiseong_case/source_data와 같은 파일)
      해면기압 · 지면온도 = Open-Meteo 과거 예보 보관(Historical Forecast API, 키 없음) 의성 관측소 위치 값.
      ASOS 시간자료의 현지기압 · 지면온도는 기상청 API 키가 있어야 받을 수 있어 대신 넣었다(README W-3)
  risk/vulnerable_ratio.json  읍면동(SGIS 행정동 코드)별 (65세 이상 + 0 ~ 14세) ÷ 총인구, SGIS 2024 성 · 연령별 인구.
      5 미만이라 가려진 칸(N/A)은 1 ~ 4의 기댓값 2.5로 채운다(README I-1)
  risk/sources.json          위 두 파일의 출처 · 받은 날 · 위치
"""

import argparse
import csv
import io
import json
import urllib.request
import zipfile
from datetime import date, datetime
from pathlib import Path

import shapefile  # pyshp

# 의성 ASOS(278) 위치: ELMFIRE 입력 step5_weather.py와 같은 근사 좌표
UISEONG_ASOS = {"stn": 278, "name": "의성", "lat": 36.356, "lon": 128.689}
OPEN_METEO = "https://historical-forecast-api.open-meteo.com/v1/forecast"
NA_FILL = 2.5  # SGIS: 인구 통계 5 미만 = N/A(0은 그대로 보임) → 1 ~ 4의 기댓값
YOUNG = range(1, 4)  # in_age_001 ~ 003 = 0 ~ 14세(5세 단위 남녀 합계)
OLD = range(14, 22)  # in_age_014 ~ 021 = 65세 이상
ALL = range(1, 22)


def read_asos(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue

    def num(r: dict, k: str, default=None):
        v = (r.get(k) or "").strip()
        return float(v) if v else default

    out = []
    for r in csv.DictReader(io.StringIO(text)):
        out.append(
            {
                "time": datetime.strptime(r["일시"].strip(), "%Y-%m-%d %H:%M").isoformat(
                    timespec="minutes"
                ),
                "temp_c": num(r, "기온(°C)"),
                "precip_mm": num(r, "강수량(mm)", 0.0),  # ASOS 빈 칸 = 비 없음
                "wind_ms": num(r, "풍속(m/s)"),
                "wind_deg": num(r, "풍향(16방위)"),
                "rh_pct": num(r, "습도(%)"),
            }
        )
    return out


def fetch_open_meteo(start: date, end: date) -> tuple[dict[str, dict], dict]:
    q = (
        f"{OPEN_METEO}?latitude={UISEONG_ASOS['lat']}&longitude={UISEONG_ASOS['lon']}"
        f"&start_date={start}&end_date={end}&hourly=pressure_msl,soil_temperature_0cm&timezone=Asia%2FSeoul"
    )
    with urllib.request.urlopen(q, timeout=60) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    h = d["hourly"]
    return {
        t: {"pressure_msl_hpa": p, "ground_temp_c": g}
        for t, p, g in zip(h["time"], h["pressure_msl"], h["soil_temperature_0cm"], strict=True)
    }, {"url": q, "grid_lat": d.get("latitude"), "grid_lon": d.get("longitude")}


def vulnerable_ratios(sgis_zip: Path, codes: set[str], uiseong: str) -> dict[str, dict]:
    """행정동 코드 → {name, total, young, old, ratio, na}. 의성(uiseong 5자리)의 읍면은 모두, 그 밖은 codes만."""
    with zipfile.ZipFile(sgis_zip) as z:
        name = next(n for n in z.namelist() if n.endswith("성연령별인구.csv"))
        cells: dict[str, dict[int, str]] = {}
        for r in csv.DictReader(io.StringIO(z.read(name).decode("cp949"))):
            c = r["행정구역코드"]
            if (
                len(c) == 8
                and (c[:5] == uiseong or c in codes)
                and r["통계항목"].startswith("in_age_0")
            ):
                i = int(r["통계항목"][-3:])
                if i in ALL:
                    cells.setdefault(c, {})[i] = r["통계값"]
        dbf = next(n for n in z.namelist() if n.endswith(".dbf") and "bnd_dong" in n)
        rd = shapefile.Reader(dbf=io.BytesIO(z.read(dbf)), encoding="utf-8")
        fields = [f[0] for f in rd.fields[1:]]
        names = {}
        for rec in rd.iterRecords():
            row = dict(zip(fields, rec, strict=True))
            names[row["ADM_CD"]] = row["ADM_NM"]
    out = {}
    for c, v in sorted(cells.items()):
        na = sum(1 for i in ALL if not str(v.get(i, "N/A")).strip().isdigit())

        def s(rng, v=v):
            return sum(int(v[i]) if str(v.get(i, "")).strip().isdigit() else NA_FILL for i in rng)

        total, young, old = s(ALL), s(YOUNG), s(OLD)
        out[c] = {
            "name": names.get(c, ""),
            "total": total,
            "young": young,
            "old": old,
            "ratio": round((young + old) / total, 4) if total else None,
            "na_cells": na,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--asos", type=Path, required=True)
    ap.add_argument("--sgis-zip", type=Path, required=True)
    a = ap.parse_args()
    out = a.input / "risk"
    out.mkdir(parents=True, exist_ok=True)
    asos = read_asos(a.asos)
    t0, t1 = asos[0]["time"][:10], asos[-1]["time"][:10]
    om, om_meta = fetch_open_meteo(date.fromisoformat(t0), date.fromisoformat(t1))
    keys = [
        "time",
        "temp_c",
        "precip_mm",
        "wind_ms",
        "wind_deg",
        "rh_pct",
        "pressure_msl_hpa",
        "ground_temp_c",
    ]
    with open(out / "weather_hourly.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in asos:
            w.writerow({**r, **om.get(r["time"], {})})
    regions = json.loads((a.input / "regions.json").read_text(encoding="utf-8"))
    uiseong = next(str(s["code"]) for s in regions["sigungu"] if s["name"] == "의성군")
    ratios = vulnerable_ratios(a.sgis_zip, {t["code"] for t in regions["townships"]}, uiseong)
    (out / "vulnerable_ratio.json").write_text(
        json.dumps(ratios, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    sources = {
        "weather_hourly.csv": {
            "asos": {"file": a.asos.name, "station": UISEONG_ASOS, "provider": "기상청 ASOS 시간자료",
                     "columns": ["temp_c", "precip_mm", "wind_ms", "wind_deg", "rh_pct"]},
            "open_meteo": {"provider": "Open-Meteo Historical Forecast API(과거 예보 보관, 키 없음)",
                           "columns": {"pressure_msl_hpa": "pressure_msl(해면기압)", "ground_temp_c": "soil_temperature_0cm(지면 0 cm)"},
                           "fetched": datetime.now().isoformat(timespec="seconds"), **om_meta},
        },
        "vulnerable_ratio.json": {
            "provider": "통계청 SGIS 2024 성 · 연령별 인구(행정동), 경계 이름 2025-2Q",
            "file": a.sgis_zip.name,
            "definition": "(in_age_001~003 0~14세 + in_age_014~021 65세 이상) ÷ in_age_001~021 합",
            "na_fill": NA_FILL,
        },
    }  # fmt: skip
    (out / "sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    missing = [r["time"] for r in asos if r["time"] not in om]
    print(f"기상 {len(asos)}시간({t0} ~ {t1}) · Open-Meteo 빈 시각 {len(missing)}")
    print(
        f"취약계층 비율 {len(ratios)}곳(의성 {sum(c.startswith(uiseong) for c in ratios)}) → {out}"
    )


if __name__ == "__main__":
    main()
