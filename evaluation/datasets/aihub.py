"""Reader for the AI Hub "산불 확산 위험 대응방안 추론 데이터" source files.

Layout under AIHUB_ROOT (…/3.개방데이터/1.데이터):
  {Training,Validation}/01.원천데이터/{TS,VS}_<size>_<region>.zip
    └ <case_id>_S_P<point>_T<hour>.json   (one file per observation point x hour)

Every file of a case repeats the same `fire_info` (ignition, final perimeter, area),
while `source_data_info.weather_conditions` changes per hour.
The final perimeter (`fire_spread_track`) is the only ground truth: there is no time series.

Usage:
  python -m evaluation.datasets.aihub list    --root $AIHUB_ROOT
  python -m evaluation.datasets.aihub extract HD20250407 --root $AIHUB_ROOT --out cases/
"""

import argparse
import csv
import json
import math
import re
import statistics
import sys
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from spread.case import FireCase, Ignition, LonLat, WeatherObs

MEMBER_RE = re.compile(r"(?P<case>[A-Z]{2}\d{8})_S_P(?P<point>\d+)_T(?P<hour>\d+)\.json$")
COORD_RE = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")
TIME_FMT = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
EARTH_RADIUS_M = 6_371_008.8
MIN_PLAUSIBLE_HUMIDITY_PCT = 5.0
AREA_MISMATCH_TOLERANCE = 0.5


@dataclass
class AihubCase:
    fire_case: FireCase
    final_perimeter: list[LonLat]
    affected_area_ha: float
    end_time: datetime | None
    response_phase: str | None
    name: str | None
    source_zip: str
    warnings: list[str] = field(default_factory=list)

    @property
    def perimeter_area_ha(self) -> float:
        return polygon_area_ha(self.final_perimeter)


def parse_time(value: str) -> datetime:
    for fmt in TIME_FMT:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised timestamp: {value!r}")


def parse_track(track: str) -> list[LonLat]:
    """`[lat,lon],[lat,lon],…` -> closed ring of (lon, lat)."""
    ring = [(float(lon), float(lat)) for lat, lon in COORD_RE.findall(track or "")]
    if len(ring) >= 3 and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def polygon_area_ha(ring: list[LonLat]) -> float:
    """Approximate area via a local equirectangular projection (fine for sanity checks)."""
    if len(ring) < 4:
        return 0.0
    lat0 = math.radians(sum(lat for _, lat in ring) / len(ring))
    xy = [
        (math.radians(lon) * EARTH_RADIUS_M * math.cos(lat0), math.radians(lat) * EARTH_RADIUS_M)
        for lon, lat in ring
    ]
    twice_area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:], strict=False))
    return abs(twice_area) / 2 / 10_000


def circular_mean_deg(angles: list[float]) -> float:
    x = sum(math.cos(math.radians(a)) for a in angles)
    y = sum(math.sin(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(y, x)) % 360


def source_zips(root: Path) -> list[Path]:
    return sorted(root.glob("*/01.원천데이터/*.zip"))


def iter_case_members(root: Path) -> Iterator[tuple[Path, str, list[str]]]:
    """Yield (zip path, case id, member names) once per case.

    The downloaded set contains duplicate archives (e.g. a re-download under another
    name), so a case seen in an earlier zip is skipped.
    """
    seen: set[str] = set()
    for zpath in source_zips(root):
        with zipfile.ZipFile(zpath) as zf:
            groups: dict[str, list[str]] = defaultdict(list)
            for name in zf.namelist():
                m = MEMBER_RE.search(name)
                if m:
                    groups[m["case"]].append(name)
        for case_id, names in groups.items():
            if case_id in seen:
                continue
            seen.add(case_id)
            yield zpath, case_id, names


def load_case(root: Path, case_id: str) -> AihubCase:
    for zpath, cid, names in iter_case_members(root):
        if cid == case_id:
            with zipfile.ZipFile(zpath) as zf:
                docs = [json.loads(zf.read(n)) for n in names]
            return build_case(case_id, docs, source_zip=zpath.name)
    raise KeyError(f"case {case_id} not found under {root}")


def build_case(case_id: str, docs: list[dict], source_zip: str) -> AihubCase:
    fire = docs[0]["raw_data_info"]["fire_info"]
    warnings: list[str] = []

    by_time: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        w = doc["source_data_info"]["weather_conditions"]
        by_time[w["observation_time"]].append(w)

    weather = []
    for obs_time in sorted(by_time):
        rows = by_time[obs_time]
        humidity = statistics.median(r["humidity_percent"] for r in rows)
        if humidity < MIN_PLAUSIBLE_HUMIDITY_PCT:
            warnings.append(f"implausible humidity {humidity}% at {obs_time}")
        weather.append(
            WeatherObs(
                time=parse_time(obs_time),
                wind_speed_ms=statistics.median(r["wind_speed"] for r in rows),
                wind_dir_deg=circular_mean_deg([r["wind_direction"] for r in rows]),
                temperature_c=statistics.median(r["temperature"] for r in rows),
                humidity_pct=humidity,
            )
        )

    start = parse_time(fire["start_timestamp"])
    loc = fire["fire_location"]
    perimeter = parse_track(fire.get("fire_spread_track", ""))
    area_ha = float(fire.get("affected_area") or 0.0)

    if len(perimeter) < 4:
        warnings.append("final perimeter has fewer than 3 vertices")
    elif area_ha:
        rel = abs(polygon_area_ha(perimeter) - area_ha) / area_ha
        if rel > AREA_MISMATCH_TOLERANCE:
            warnings.append(
                f"perimeter area {polygon_area_ha(perimeter):.1f} ha vs recorded {area_ha} ha"
            )

    return AihubCase(
        fire_case=FireCase(
            case_id=case_id,
            ignition=Ignition(lon=loc["lon"], lat=loc["lat"], time=start),
            t0=start,
            weather=weather,
        ),
        final_perimeter=perimeter,
        affected_area_ha=area_ha,
        end_time=parse_time(fire["end_timestamp"]) if fire.get("end_timestamp") else None,
        response_phase=fire.get("response_phase"),
        name=fire.get("incident_name"),
        source_zip=source_zip,
        warnings=warnings,
    )


def write_case(case: AihubCase, out_dir: Path) -> Path:
    """Write ignition/perimeter GeoJSON, hourly weather CSV and meta JSON for one case."""
    target = out_dir / case.fire_case.case_id
    target.mkdir(parents=True, exist_ok=True)
    ign = case.fire_case.ignition

    def feature(geometry: dict, **props: object) -> dict:
        return {"type": "Feature", "geometry": geometry, "properties": props}

    (target / "ignition.geojson").write_text(
        json.dumps(
            feature({"type": "Point", "coordinates": [ign.lon, ign.lat]}, time=ign.time.isoformat())
        )
    )
    (target / "perimeter.geojson").write_text(
        json.dumps(
            feature(
                {"type": "Polygon", "coordinates": [case.final_perimeter]},
                kind="final",
                area_ha=case.affected_area_ha,
            )
        )
    )
    with (target / "weather.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "wind_speed_ms", "wind_dir_deg", "temperature_c", "humidity_pct"])
        for w in case.fire_case.weather:
            writer.writerow(
                [
                    w.time.isoformat(),
                    w.wind_speed_ms,
                    round(w.wind_dir_deg, 1),
                    w.temperature_c,
                    w.humidity_pct,
                ]
            )
    meta = {
        "case_id": case.fire_case.case_id,
        "name": case.name,
        "source_zip": case.source_zip,
        "start": case.fire_case.t0.isoformat(),
        "end": case.end_time.isoformat() if case.end_time else None,
        "affected_area_ha": case.affected_area_ha,
        "perimeter_area_ha": round(case.perimeter_area_ha, 2),
        "perimeter_vertices": max(len(case.final_perimeter) - 1, 0),
        "weather_hours": len(case.fire_case.weather),
        "response_phase": case.response_phase,
        "warnings": case.warnings,
    }
    (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return target


def list_cases(root: Path) -> list[dict]:
    rows = []
    for zpath, case_id, names in iter_case_members(root):
        with zipfile.ZipFile(zpath) as zf:
            fire = json.loads(zf.read(names[0]))["raw_data_info"]["fire_info"]
        rows.append(
            {
                "case_id": case_id,
                "zip": zpath.stem,
                "area_ha": fire.get("affected_area"),
                "vertices": len(parse_track(fire.get("fire_spread_track", ""))) - 1,
                "files": len(names),
                "phase": fire.get("response_phase"),
                "lat": fire["fire_location"]["lat"],
                "lon": fire["fire_location"]["lon"],
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evaluation.datasets.aihub")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="summarise every case")
    p_list.add_argument("--root", type=Path, required=True)
    p_ext = sub.add_parser("extract", help="write one case to disk")
    p_ext.add_argument("case_id")
    p_ext.add_argument("--root", type=Path, required=True)
    p_ext.add_argument("--out", type=Path, default=Path("cases"))
    args = parser.parse_args()

    if args.cmd == "list":
        rows = sorted(list_cases(args.root), key=lambda r: r["area_ha"] or 0)
        writer = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    else:
        case = load_case(args.root, args.case_id)
        target = write_case(case, args.out)
        print(f"wrote {target}")
        for w in case.warnings:
            print(f"WARNING: {w}")


if __name__ == "__main__":
    main()
