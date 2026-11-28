"""ELMFIRE 확산속도 배율(adj) — 서비스 기본값과 환경 변수 ELMFIRE_ADJ 읽기.

덱 adj.tif에 이 값을 곱해 실행 폴더에만 쓴다(spread.perimeter_run.run). A′ 덱의 adj.tif는 1이라
이 값이 곧 배율이다.

기본 1.25: 미국 GOFER 26건 검증(1시간마다 실측 화선에서 다시 출발, 1·5·8시간 채점)에서
1.0배 대비 재현율 0.720 → 0.745(22건 모두 상승), F2 0.705 → 0.710. 놓친 곳(미탐지)이 대피 누락으로
이어지므로 덜 놓치는 쪽을 택했다. 대신 넓게 잡는 정도(면적비)가 1.01 → 1.18배로 커진다.

이 모듈은 아무것도 불러오지 않는다 — 설정(app.core.config)과 worker(app.predict.worker_server)가
같이 쓴다.
"""

import math

DEFAULT_ADJ = 1.25
ENV_KEY = "ELMFIRE_ADJ"


def parse_adj(value: str | float | None) -> float:
    """환경 변수 값을 배율로. 비었으면 기본값, 0 이하 · 숫자 아님 · 무한이면 ValueError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_ADJ
    try:
        adj = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{ENV_KEY}는 숫자여야 합니다: {value!r}") from None
    if not math.isfinite(adj) or adj <= 0:
        raise ValueError(f"{ENV_KEY}는 0보다 큰 수여야 합니다: {value!r}")
    return adj
