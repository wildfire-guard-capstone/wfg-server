from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from common import DEFAULT_CONFIG


def call(script: str, config: str, extra: list[str] | None = None) -> None:
    command = [sys.executable, str(Path(__file__).parent / script), "--config", config]
    command.extend(extra or [])
    print("\n###", script, flush=True)
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="의성 ELMFIRE 재현 파이프라인 전체 실행")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--prepare-only", action="store_true", help="입력자료까지만 생성")
    parser.add_argument("--scenario", action="append", help="실행할 시나리오. 여러 번 지정 가능")
    parser.add_argument("--force", action="store_true", help="기존 선택 시나리오 결과를 지우고 재실행")
    args = parser.parse_args()

    call("00_validate.py", args.config)
    for script in ["01_prepare_terrain.py", "02_prepare_weather.py", "03_prepare_landcover.py",
                   "04_prepare_forest.py", "05_build_landscapes.py"]:
        call(script, args.config)
    if args.prepare_only:
        print("입력자료 생성까지만 완료했습니다.")
        return
    extra = []
    for scenario in args.scenario or []:
        extra.extend(["--scenario", scenario])
    if args.force:
        extra.append("--force")
    call("06_run_scenarios.py", args.config, extra)
    post = []
    for scenario in args.scenario or []:
        post.extend(["--scenario", scenario])
    call("07_postprocess.py", args.config, post)


if __name__ == "__main__":
    main()
