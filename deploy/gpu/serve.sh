#!/usr/bin/env bash
# GPU 서버: EC2가 부를 서비스 켜기 · 끄기 — vLLM(EXAONE 4.5, 18041) · 근거 서버(18042) · 근거 DB.
#   bash ~/wfg/tunnel/serve.sh start | stop | status
# - 8031 · 8032는 다른 계정이 쓰고 있어(2026-11-09부터) 18041 · 18042를 쓴다. EC2 쪽 포트는
#   tunnel.sh가 8031 · 8032로 이어 준다
# - 공용 GPU다. 쓸 때만 start, 끝나면 stop. 이 스크립트가 켠 것만 끈다
# - vLLM은 가장 빈 GPU 한 장에 띄운다(가중치 20.4 GiB + KV). 27 GB가 안 비면 켜지 않는다
set -u
export WFG_LLM_PORT=18041
EVP=18042
source ~/wfg/rag-pipeline/deploy/server/env.sh
DIR=$HOME/wfg/tunnel
LOGS=$DIR/logs
mkdir -p "$LOGS"
LLM_URL=http://127.0.0.1:$WFG_LLM_PORT
EV_URL=http://127.0.0.1:$EVP
EV_PID=$DIR/evidence.pid
NEED=27000

llm_up() { curl -sf -m 3 $LLM_URL/v1/models | grep -q "\"$WFG_LLM_NAME\""; }
ev_up() { curl -sf -m 3 $EV_URL/health | grep -q build; }

case "${1:-status}" in
  start)
    bash ~/wfg/rag-pipeline/deploy/server/db.sh start | tail -1
    if ev_up; then
      echo "근거 서버 이미 켜져 있음"
    else
      nohup setsid "$WFG_PY" ~/wfg/rag-pipeline/serve_evidence.py --device cpu --port $EVP \
        > "$LOGS/evidence.log" 2>&1 < /dev/null &
      echo $! > "$EV_PID"
      for i in $(seq 1 60); do ev_up && break; sleep 5; done
      ev_up || { echo "[실패] 근거 서버 — $LOGS/evidence.log"; exit 1; }
    fi
    echo "근거 서버: $(curl -s -m 3 $EV_URL/health)"
    if llm_up; then
      echo "vLLM 이미 켜져 있음"
    else
      read -r idx free total < <(nvidia-smi --query-gpu=index,memory.free,memory.total \
        --format=csv,noheader,nounits | tr -d ' ' | sort -t, -k2 -n -r | head -1 | tr ',' ' ')
      if [ "$free" -lt $NEED ]; then
        echo "[중단] 가장 빈 GPU $idx 가 ${free} MiB(필요 $NEED MiB) — 다른 사용자가 쓰는 중"
        exit 1
      fi
      UTIL=$(awk -v f="$free" -v t="$total" 'BEGIN { printf "%.2f", (f - 1500) / t }')
      bash ~/wfg/rag-pipeline/deploy/server/llm.sh start --gpu-memory-utilization "$UTIL" || exit 1
      llm_up || { echo "[실패] $LLM_URL 의 모델이 EXAONE이 아님"; exit 1; }
    fi
    echo "준비됨: vLLM $LLM_URL · 근거 $EV_URL"
    ;;
  stop)
    if [ -f "$EV_PID" ]; then
      PID=$(cat "$EV_PID")
      kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null
      rm -f "$EV_PID"
      echo "근거 서버 중지"
    fi
    bash ~/wfg/rag-pipeline/deploy/server/llm.sh stop
    bash ~/wfg/rag-pipeline/deploy/server/db.sh stop | tail -1
    ;;
  status)
    llm_up && echo "vLLM 켜짐 ($LLM_URL)" || echo "vLLM 꺼짐"
    ev_up && echo "근거 서버 켜짐 ($EV_URL)" || echo "근거 서버 꺼짐"
    nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader
    ;;
  *) echo "사용법: bash serve.sh start|stop|status"; exit 2 ;;
esac
