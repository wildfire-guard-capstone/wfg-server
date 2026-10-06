from __future__ import annotations

import argparse
import csv
from datetime import datetime

from common import DEFAULT_CONFIG, load_context, resolve, write_constant_bands


def read_asos(path, start: datetime, count: int):
    selected = []
    text = None
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise RuntimeError(f"ASOS CSV 인코딩을 읽을 수 없습니다: {path}")
    reader = csv.DictReader(text.splitlines())
    for row in reader:
        stamp = datetime.strptime(row["일시"], "%Y-%m-%d %H:%M")
        if stamp >= start and len(selected) < count:
            selected.append(row)
    if len(selected) != count:
        raise RuntimeError(f"ASOS에서 {count}시간을 찾지 못했습니다: {len(selected)}시간")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description="ASOS CSV로 시간별 기상 래스터를 만듭니다.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--wind-profile", choices=["legacy_knots", "mph_recommended"],
                        help="생략하면 pipeline_config.json의 프로필을 사용합니다.")
    parser.add_argument("--suffix", default="hourly", help="출력 이름 접미사")
    args = parser.parse_args()
    cfg, root = load_context(args.config)
    weather = cfg["weather"]
    profile = args.wind_profile or weather["wind_speed_profile"]
    factor = weather["wind_speed_factors"][profile]
    rows = read_asos(resolve(root, cfg["sources"]["asos"]),
                     datetime.strptime(weather["start"], "%Y-%m-%d %H:%M"),
                     int(weather["band_count"]))
    ws = [float(row["풍속(m/s)"] or 0) * factor for row in rows]
    wd = [float(row["풍향(16방위)"] or 0) for row in rows]
    inputs = root / "inputs"
    reference = inputs / "dem.tif"
    outputs = {
        "ws": ws,
        "wd": wd,
        "m1": weather["m1_percent"],
        "m10": weather["m10_percent"],
        "m100": weather["m100_percent"]
    }
    for name, values in outputs.items():
        write_constant_bands(reference, inputs / f"{name}_{args.suffix}.tif", values)
    print(f"기상 입력 완료: {len(rows)}개 밴드, 풍속 프로필={profile}, 계수={factor}")
    print("풍속:", ", ".join(f"{value:.2f}" for value in ws))
    print("풍향:", ", ".join(f"{value:.0f}" for value in wd))


if __name__ == "__main__":
    main()
