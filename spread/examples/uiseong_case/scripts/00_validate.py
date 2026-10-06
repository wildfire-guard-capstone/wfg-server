from __future__ import annotations

import argparse
from pathlib import Path

from common import DEFAULT_CONFIG, load_context, resolve


def main() -> None:
    parser = argparse.ArgumentParser(description="의성 ELMFIRE 원자료와 설정을 점검합니다.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    cfg, root = load_context(args.config)

    failures = []
    print(f"사례 폴더: {root}")
    for name, value in cfg["sources"].items():
        path = resolve(root, value)
        ok = path.is_file()
        if name == "forest" and not ok:
            archive = path.parent.parent / "47730.zip"
            ok = archive.is_file()
            if ok:
                print(f"[OK] forest archive: {archive} (실행 시 자동 압축 해제)")
                continue
        print(f"[{ 'OK' if ok else '없음' }] {name}: {path}")
        if not ok:
            failures.append(path)
    for scenario in cfg["scenarios"]:
        path = root / "inputs" / scenario["config"]
        ok = path.is_file()
        print(f"[{ 'OK' if ok else '없음' }] 설정 {scenario['name']}: {path.name}")
        if not ok:
            failures.append(path)
    if failures:
        raise SystemExit(f"필요 파일 {len(failures)}개가 없습니다.")
    print("검증 완료: 필수 원자료와 시나리오 설정 파일이 있습니다.")


if __name__ == "__main__":
    main()
