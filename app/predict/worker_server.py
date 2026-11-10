"""ELMFIRE worker의 받는 곳(구조 B) — API가 만든 기상 입력으로 ELMFIRE를 돌려 P1 ~ P8을 돌려준다.

EC2: worker 컨테이너(ELMFIRE · rasterio · 덱)에서 `python -m app.predict.worker_server`로 띄운다.
API(app.predict.elmfire_remote)가 같은 Docker 네트워크 안에서만 부른다(포트를 밖에 열지 않는다).

  GET  /health → {"ok": true, "busy": false}
  POST /run    → 몸통 = 예측 요청(npz, app.predict.elmfire_wire) → {"slices": P1 ~ P8, "meta"}
                 오류: 422 PREDICTION_INVALID(입력 · 화선 문제) · 504 ELMFIRE_TIMEOUT ·
                       500 ELMFIRE_FAILED

한 번에 1회씩 돌린다(메모리 약 1.1 GB) — 뒤 요청은 앞 실행이 끝날 때까지 기다린다.
표준 라이브러리 HTTP 서버라 worker 이미지에 패키지를 더하지 않는다.
설정(환경 변수): PREDICTOR_DECK(기본 /deck) · ELMFIRE_BIN(기본 elmfire_1.1) ·
PREDICTOR_WORK_DIR(실행 폴더, 기본 /tmp/runs) · ELMFIRE_TIMEOUT_S(기본 300) ·
WORKER_PORT(기본 8100).
"""

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.predict.base import Predictor
from app.predict.elmfire_wire import pack_error, pack_result, unpack_request

log = logging.getLogger("elmfire-worker")
MAX_BODY = 64 * 1024 * 1024  # 요청 몸통 상한(의성 덱 기상 입력은 압축해 1 MB 안팎)


def predictor_from_env() -> Predictor:
    """환경 변수로 ELMFIRE 예측을 만든다. spread · rasterio는 worker 이미지에만 있다."""
    from app.predict.elmfire import ElmfirePredictor

    return ElmfirePredictor(
        deck=Path(os.environ.get("PREDICTOR_DECK", "/deck")),
        elmfire=os.environ.get("ELMFIRE_BIN", "elmfire_1.1"),
        work_dir=Path(os.environ.get("PREDICTOR_WORK_DIR", "/tmp/runs")),
        timeout_s=float(os.environ.get("ELMFIRE_TIMEOUT_S", "300")),
    )


def make_server(address: tuple[str, int], predictor: Predictor) -> ThreadingHTTPServer:
    """받는 곳을 만든다. 상태 확인은 실행 중에도 바로 답하고, 실행은 잠금으로 하나씩."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — http.server 규칙
            if self.path != "/health":
                self._send(404, pack_error("NOT_FOUND", self.path))
                return
            self._send(200, json.dumps({"ok": True, "busy": lock.locked()}).encode())

        def do_POST(self) -> None:  # noqa: N802
            """1. 몸통 읽기 · 풀기 2. 잠금(앞 실행을 기다림) 안에서 ELMFIRE 3. 결과 보내기."""
            if self.path != "/run":
                self._send(404, pack_error("NOT_FOUND", self.path))
                return
            n = int(self.headers.get("Content-Length") or 0)  # 1
            if not 0 < n <= MAX_BODY:
                self._send(413, pack_error("PREDICTION_INVALID", f"요청 크기 {n} 바이트"))
                return
            try:
                request = unpack_request(self.rfile.read(n))
            except ValueError as e:
                self._send(422, pack_error("PREDICTION_INVALID", str(e)))
                return
            asked = time.monotonic()
            with lock:  # 2
                waited = time.monotonic() - asked
                status, body = self._run(request, waited)
            self._send(status, body)  # 3

        def _run(self, request, waited: float) -> tuple[int, bytes]:
            try:
                result = predictor.predict(request)
            except ValueError as e:
                return 422, pack_error("PREDICTION_INVALID", str(e))
            except TimeoutError as e:
                return 504, pack_error("ELMFIRE_TIMEOUT", str(e))
            except Exception as e:  # noqa: BLE001 — 어떤 실패든 API에 알린다
                log.exception("ELMFIRE 실행 실패")
                return 500, pack_error("ELMFIRE_FAILED", f"{type(e).__name__}: {str(e)[:500]}")
            log.info("P1 ~ P8 완료: ELMFIRE %s초, 기다림 %.1f초", result.meta.get("wall_s"), waited)
            return 200, pack_result(
                result.slices, {**result.meta, "queue_wait_s": round(waited, 1)}
            )

        def log_message(self, fmt: str, *args) -> None:
            log.info("%s %s", self.address_string(), fmt % args)

    return ThreadingHTTPServer(address, Handler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    port = int(os.environ.get("WORKER_PORT", "8100"))
    # 컨테이너 안의 모든 주소에서 받는다 — compose가 포트를 밖에 열지 않아 같은 네트워크만 닿는다
    server = make_server(("0.0.0.0", port), predictor_from_env())  # noqa: S104
    log.info("ELMFIRE worker 시작: 포트 %s", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
