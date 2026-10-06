from __future__ import annotations

import argparse
import shutil
import subprocess

from common import DEFAULT_CONFIG, load_context


def main() -> None:
    parser = argparse.ArgumentParser(description="선택한 ELMFIRE 시나리오를 실행합니다.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--scenario", action="append", help="여러 번 지정 가능. 생략하면 전체 실행")
    parser.add_argument("--force", action="store_true", help="해당 결과 폴더를 비우고 다시 실행")
    args = parser.parse_args()
    cfg, root = load_context(args.config)
    selected = set(args.scenario or [item["name"] for item in cfg["scenarios"]])
    known = {item["name"] for item in cfg["scenarios"]}
    unknown = selected - known
    if unknown:
        raise SystemExit(f"알 수 없는 시나리오: {', '.join(sorted(unknown))}")

    for item in cfg["scenarios"]:
        if item["name"] not in selected:
            continue
        config_path = root / "inputs" / item["config"]
        output = (root / item["output"]).resolve()
        if root.resolve() not in output.parents:
            raise RuntimeError(f"안전하지 않은 출력 경로: {output}")
        existing = output / "fire_size_stats.csv"
        if existing.exists() and not args.force:
            print(f"건너뜀: {item['name']} (기존 결과 있음, --force로 재실행)")
            continue
        if args.force and output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        print(f"\n===== {item['name']} =====", flush=True)
        subprocess.run(["elmfire_1.1", f"./inputs/{config_path.name}"], cwd=root, check=True)
    print("요청한 시나리오 실행이 끝났습니다.")


if __name__ == "__main__":
    main()
