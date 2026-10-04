"""Reader for the AI Hub "산불 확산 위험 대응방안 추론 데이터" source files.

Layout under AIHUB_ROOT (…/3.개방데이터/1.데이터):
  {Training,Validation}/01.원천데이터/{TS,VS}_<size>_<region>.zip
    └ <case_id>_S_P<point>_T<hour>.json   (one file per observation point x hour)

Every file of a case repeats the same `fire_info` (ignition, final perimeter, area),
while `source_data_info.weather_conditions` changes per hour.
The final perimeter (`fire_spread_track`) is the only ground truth: there is no time series.

Ground-truth perimeters are graded (A/B/C) by `validate.check_perimeter`; weather
records outside physical ranges are dropped or blanked with a warning.

Usage:
  python -m evaluation.datasets.aihub list    --root $AIHUB_ROOT [--grade A,B] [--out grades.csv]
  python -m evaluation.datasets.aihub extract SC20230303 --root $AIHUB_ROOT --out cases/
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

from shapely.geometry import mapping
from shapely.ops import transform

from evaluation.datasets.validate import PerimeterCheck, check_perimeter
from spread.case import FireCase, Ignition, LonLat, WeatherObs
from spread.geo import projector

MEMBER_RE = re.compile(r"(?P<case>[A-Z]{2}\d{8})_S_P(?P<point>\d+)_T(?P<hour>\d+)\.json$")
COORD_RE = re.compile(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")
TIME_FMT = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
# Weather sanity ranges. Out-of-range humidity is blanked (e.g. 2 % in HD20250407),
# out-of-range wind drops the hour, since wind drives the spread direction.
HUMIDITY_RANGE_PCT = (5.0, 100.0)
WIND_SPEED_RANGE_MS = (0.0, 40.0)
MAX_WEATHER_GAP_H = 1.5


@dataclass
class AihubCase:
    fire_case: FireCase
    final_perimeter: list[LonLat]
    affected_area_ha: float
    end_time: datetime | None
    response_phase: str | None
    name: str | None
    source_zip: str
    check: PerimeterCheck
    warnings: list[str] = field(default_factory=list)
    # Station names only (e.g. "순천"); coordinates come from the KMA station table later,
    # to measure how far the wind record is from the fire (plan step 3-3).
    weather_stations: list[str] = field(default_factory=list)
    # One row per observation point: lon, lat, fuel_type, fuel_moisture (median over hours,
    # 0 = missing), canopy_coverage (%), canopy_height (m). Case-level fuel, moisture, canopy.
    points: list[dict] = field(default_factory=list)


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
    stations = sorted(
        {w.get("observatory_location") for ws in by_time.values() for w in ws} - {None, ""}
    )
    points = collect_points(docs)

    weather = []
    for obs_time in sorted(by_time):
        obs = clean_weather(obs_time, by_time[obs_time], warnings)
        if obs is not None:
            weather.append(obs)

    start = parse_time(fire["start_timestamp"])
    end = parse_time(fire["end_timestamp"]) if fire.get("end_timestamp") else None
    warnings.extend(weather_coverage(weather, start, end))
    loc = fire["fire_location"]
    perimeter = parse_track(fire.get("fire_spread_track", ""))
    area_ha = float(fire.get("affected_area") or 0.0)
    check = check_perimeter(perimeter, (loc["lon"], loc["lat"]), area_ha or None, start, end)

    return AihubCase(
        fire_case=FireCase(
            case_id=case_id,
            ignition=Ignition(lon=loc["lon"], lat=loc["lat"], time=start),
            t0=start,
            weather=weather,
        ),
        final_perimeter=perimeter,
        affected_area_ha=area_ha,
        end_time=end,
        response_phase=fire.get("response_phase"),
        name=fire.get("incident_name"),
        source_zip=source_zip,
        check=check,
        warnings=warnings,
        weather_stations=stations,
        points=points,
    )


POINT_FIELDS = (
    "point", "lon", "lat", "fuel_type", "fuel_moisture", "canopy_coverage", "canopy_height"
)  # fmt: skip
POINT_RE = re.compile(r"_P(?P<point>\d+)_T")


def collect_points(docs: list[dict]) -> list[dict]:
    """Observation points with their fuel attributes (same point repeats every hour)."""
    by_point: dict[str, dict] = {}
    moisture: dict[str, list[float]] = defaultdict(list)
    for doc in docs:
        src = doc.get("source_data_info") or {}
        m = POINT_RE.search(src.get("file_name") or "")
        fuel = src.get("fuel_conditions") or {}
        loc = (src.get("user_info") or {}).get("query_location") or {}
        if not m or "lon" not in loc:
            continue
        pid = m["point"]
        by_point.setdefault(
            pid,
            {
                "point": pid,
                "lon": loc["lon"],
                "lat": loc["lat"],
                "fuel_type": fuel.get("fuel_type") or "",
                "canopy_coverage": fuel.get("canopy_coverage"),
                "canopy_height": fuel.get("canopy_height"),
            },
        )
        if fuel.get("fuel_moisture") is not None:
            moisture[pid].append(float(fuel["fuel_moisture"]))
    for pid, row in by_point.items():
        row["fuel_moisture"] = statistics.median(moisture[pid]) if moisture[pid] else None
    return [by_point[k] for k in sorted(by_point)]


def weather_coverage(weather: list[WeatherObs], start: datetime, end: datetime | None) -> list[str]:
    """Report gaps the spread model will have to fill (ELMFIRE needs one record per hour)."""
    if not weather:
        return ["no usable weather records"]
    issues = []
    lead_h = (weather[0].time - start).total_seconds() / 3600
    if lead_h > MAX_WEATHER_GAP_H:
        issues.append(f"first weather record {lead_h:.1f} h after ignition")
    for prev, cur in zip(weather, weather[1:], strict=False):
        gap_h = (cur.time - prev.time).total_seconds() / 3600
        if gap_h > MAX_WEATHER_GAP_H:
            issues.append(f"weather gap {gap_h:.0f} h after {prev.time:%m-%d %H:%M}")
    if end is not None:
        tail_h = (end - weather[-1].time).total_seconds() / 3600
        if tail_h > MAX_WEATHER_GAP_H:
            issues.append(f"last weather record {tail_h:.1f} h before end")
    return issues


def clean_weather(obs_time: str, rows: list[dict], warnings: list[str]) -> WeatherObs | None:
    """Median over observation points for one hour, with range checks."""
    speed = statistics.median(r["wind_speed"] for r in rows)
    if not WIND_SPEED_RANGE_MS[0] <= speed <= WIND_SPEED_RANGE_MS[1]:
        warnings.append(f"dropped {obs_time}: wind speed {speed} m/s out of range")
        return None
    humidity: float | None = statistics.median(r["humidity_percent"] for r in rows)
    if not HUMIDITY_RANGE_PCT[0] <= humidity <= HUMIDITY_RANGE_PCT[1]:
        warnings.append(f"blanked humidity {humidity}% at {obs_time}")
        humidity = None
    return WeatherObs(
        time=parse_time(obs_time),
        wind_speed_ms=speed,
        wind_dir_deg=circular_mean_deg([r["wind_direction"] for r in rows]),
        temperature_c=statistics.median(r["temperature"] for r in rows),
        humidity_pct=humidity,
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
                kind="final_raw",
                area_ha=case.affected_area_ha,
            )
        )
    )
    if case.check.repaired_utm is not None:
        repaired = transform(projector(case.check.epsg, inverse=True), case.check.repaired_utm)
        (target / "perimeter_repaired.geojson").write_text(
            json.dumps(
                feature(
                    mapping(repaired),
                    kind="final_repaired",
                    area_ha=round(case.check.repaired_area_ha, 2),
                    grade=case.check.grade.value,
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
    if case.points:
        with (target / "points.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(POINT_FIELDS))
            writer.writeheader()
            writer.writerows(case.points)
    meta = {
        "case_id": case.fire_case.case_id,
        "name": case.name,
        "source_zip": case.source_zip,
        "start": case.fire_case.t0.isoformat(),
        "end": case.end_time.isoformat() if case.end_time else None,
        "weather_hours": len(case.fire_case.weather),
        "response_phase": case.response_phase,
        "perimeter_check": case.check.as_row(),
        "weather_stations": case.weather_stations,
        "warnings": case.warnings,
    }
    (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return target


def list_cases(root: Path) -> list[dict]:
    rows = []
    for zpath, case_id, names in iter_case_members(root):
        with zipfile.ZipFile(zpath) as zf:
            fire = json.loads(zf.read(names[0]))["raw_data_info"]["fire_info"]
        loc = fire["fire_location"]
        check = check_perimeter(
            parse_track(fire.get("fire_spread_track", "")),
            (loc["lon"], loc["lat"]),
            float(fire.get("affected_area") or 0.0) or None,
            parse_time(fire["start_timestamp"]),
            parse_time(fire["end_timestamp"]) if fire.get("end_timestamp") else None,
        )
        row = {
            "case_id": case_id,
            "split": zpath.parent.parent.name,
            "zip": zpath.stem,
            "phase": fire.get("response_phase"),
            "lat": loc["lat"],
            "lon": loc["lon"],
            "files": len(names),
        }
        row.update(check.as_row())
        for key in ("raw_area_ha", "repaired_area_ha", "area_ratio", "ignition_distance_m"):
            if isinstance(row[key], float):
                row[key] = round(row[key], 2)
        if row["duration_h"] is not None:
            row["duration_h"] = round(row["duration_h"], 1)
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evaluation.datasets.aihub")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="summarise and grade every case")
    p_list.add_argument("--root", type=Path, required=True)
    p_list.add_argument("--grade", help="comma-separated grades to keep, e.g. A,B")
    p_list.add_argument("--out", type=Path, help="write CSV here instead of stdout")
    p_ext = sub.add_parser("extract", help="write one case to disk")
    p_ext.add_argument("case_id")
    p_ext.add_argument("--root", type=Path, required=True)
    p_ext.add_argument("--out", type=Path, default=Path("cases"))
    args = parser.parse_args()

    if args.cmd == "list":
        rows = sorted(list_cases(args.root), key=lambda r: r["recorded_area_ha"] or 0)
        if args.grade:
            keep = set(args.grade.split(","))
            rows = [r for r in rows if r["grade"] in keep]
        out = args.out.open("w", newline="") if args.out else sys.stdout
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        if args.out:
            out.close()
            counts = {g: sum(r["grade"] == g for r in rows) for g in ("A", "B", "C")}
            print(f"wrote {len(rows)} cases to {args.out} {counts}")
    else:
        case = load_case(args.root, args.case_id)
        target = write_case(case, args.out)
        print(f"wrote {target}  grade={case.check.grade.value}")
        for reason in case.check.reasons:
            print(f"  perimeter: {reason}")
        for w in case.warnings:
            print(f"  WARNING: {w}")


if __name__ == "__main__":
    main()
