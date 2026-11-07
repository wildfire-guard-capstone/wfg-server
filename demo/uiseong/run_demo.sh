#!/usr/bin/env bash
# GPU 서버에서 의성 데모 전체 실행. 근거 검색 서버(8032)와 LLM(8031)은 먼저 켜 둔다(README 참고).
#   bash demo/uiseong/run_demo.sh [run_demo.py 인자…]      예: --rounds 1 --skip-llm
# 경로는 환경변수로 바꿀 수 있다: DEMO_HOME(기본 ~/wfg/demo) · ELMFIRE_BIN · ELM_ENV
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"          # wfg-server 루트
DEMO_HOME="${DEMO_HOME:-$HOME/wfg/demo}"
ELM_ENV="${ELM_ENV:-$HOME/miniforge3/envs/wfg-elm}"  # python 3.12 · GDAL < 3.11 · rasterio · shapely · gfortran · openmpi
ELMFIRE_BIN="${ELMFIRE_BIN:-$HOME/wfg/elmfire/bin/elmfire}"
export PATH="$(dirname "$ELMFIRE_BIN"):$ELM_ENV/bin:$PATH"
export PROJ_DATA="$ELM_ENV/share/proj" GDAL_DATA="$ELM_ENV/share/gdal"
export PYTHONPATH="$HERE" PYTHONUTF8=1 PYTHONUNBUFFERED=1
OUT="${OUT:-$DEMO_HOME/runs/$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
cd "$HERE"
python -m demo.uiseong.run_demo --data "$DEMO_HOME/data" --deck "$DEMO_HOME/deck" \
  --elmfire "$ELMFIRE_BIN" --out "$OUT" "$@" 2>&1 | tee "$OUT/run.log"
