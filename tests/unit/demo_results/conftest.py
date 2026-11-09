from pathlib import Path

import pytest

from demo.uiseong.export_bundle import build, write

from .results_example import make_run


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    """가짜 데모 결과를 내보낸 결과 조회 API 묶음 폴더."""
    run, inp = make_run(tmp_path)
    out = tmp_path / "bundle"
    write(build(run, inp), out)
    return out
