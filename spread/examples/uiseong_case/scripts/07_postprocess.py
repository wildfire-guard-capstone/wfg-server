from __future__ import annotations

import argparse
import csv
from pathlib import Path

from common import DEFAULT_CONFIG, gdal_modules, load_context, run, write_array_like


def remove_dataset(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-shm"), Path(str(path) + "-wal")):
        if candidate.exists():
            candidate.unlink()


def latest_toa(folder: Path) -> Path | None:
    files = sorted(folder.glob("time_of_arrival_*.bil"))
    return files[-1] if files else None


def main() -> None:
    parser = argparse.ArgumentParser(description="도착시간 GeoTIFF·시간대별 등시선·요약표를 만듭니다.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--scenario", action="append", help="생략하면 결과가 있는 전체 시나리오")
    args = parser.parse_args()
    cfg, root = load_context(args.config)
    selected = set(args.scenario or [item["name"] for item in cfg["scenarios"]])
    gdal, _ = gdal_modules()
    import numpy as np

    dem = root / "inputs" / "dem.tif"
    dem_ds = gdal.Open(str(dem))
    gt = dem_ds.GetGeoTransform()
    ignition = cfg["ignition"]
    pixel_x = int((ignition["x"] - gt[0]) / gt[1])
    pixel_y = int((ignition["y"] - gt[3]) / gt[5])
    summaries = []

    for item in cfg["scenarios"]:
        if item["name"] not in selected:
            continue
        folder = root / item["output"]
        toa = latest_toa(folder)
        if toa is None:
            print(f"건너뜀: {item['name']} (도착시간 BIL 없음)")
            continue
        arr = gdal.Open(str(toa)).ReadAsArray().astype(np.float32)
        arr[arr < 0] = -9999
        if 0 <= pixel_y < arr.shape[0] and 0 <= pixel_x < arr.shape[1]:
            arr[pixel_y, pixel_x] = 0
        fixed = folder / f"time_of_arrival_{item['name']}_fixed.tif"
        write_array_like(dem, fixed, arr, gdal.GDT_Float32, -9999)

        raw = folder / f"hourly_isochrones_{item['name']}.gpkg"
        clean = folder / f"hourly_isochrones_{item['name']}_clean.gpkg"
        remove_dataset(raw)
        remove_dataset(clean)
        levels = [str(int(hour * 3600)) for hour in cfg["contour_hours"]]
        run(["gdal_contour", "-a", "time_s", "-fl", *levels, "-snodata", "-9999",
             "-f", "GPKG", "-nln", "contours", str(fixed), str(raw)])
        sql = (
            "SELECT time_s, ST_Collect(geom) AS geom "
            "FROM contours GROUP BY time_s ORDER BY time_s"
        )
        run(["ogr2ogr", "-f", "GPKG", "-overwrite", "-nln", "contours", "-a_srs", cfg["crs"],
             "-dialect", "SQLite", "-sql", sql, str(clean), str(raw)])

        stats = folder / "fire_size_stats.csv"
        if stats.exists():
            with stats.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            if rows:
                last = rows[-1]
                summaries.append({
                    "scenario": item["name"],
                    "simulation_hours": last.get("tstop (h)", ""),
                    "wall_clock_seconds": last.get("Wall clock time (s)", ""),
                    "total_fire_area_ac": last.get("Total fire area (ac)", ""),
                    "crown_fire_area_ac": last.get("Crown fire area (ac)", ""),
                    "embers": last.get("Nembers", "")
                })
        print(f"후처리 완료: {item['name']}")

    summary_path = root / "scenario_summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["scenario", "simulation_hours", "wall_clock_seconds", "total_fire_area_ac",
                  "crown_fire_area_ac", "embers"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)
    print(f"요약표: {summary_path}")


if __name__ == "__main__":
    main()
