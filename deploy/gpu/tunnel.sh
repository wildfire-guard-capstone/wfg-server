#!/usr/bin/env bash
# GPU 서버 → EC2 역방향 SSH 터널. 학교는 들어오는 접속을 막으므로 GPU 서버가 EC2로 건다.
#   bash ~/wfg/tunnel/tunnel.sh start | stop | status
# EC2의 docker0(172.17.0.1) 8031 · 8032 → 이 서버의 vLLM 18041 · 근거 서버 18042.
# - EC2 주소: ~/wfg/tunnel/ec2_host 한 줄(EC2를 중지 → 시작하면 IP가 바뀌니 고친다)
# - 키: ~/.ssh/wfg_tunnel_ed25519(터널 전용. EC2 authorized_keys에 포트 전달만 되게 등록)
# - 끊기면 15초 뒤 다시 건다(sudo · autossh 없이)
set -u
DIR=$HOME/wfg/tunnel
LOGS=$DIR/logs
mkdir -p "$LOGS"
KEY=$HOME/.ssh/wfg_tunnel_ed25519
HOSTF=$DIR/ec2_host
PIDF=$DIR/tunnel.pid
BIND=172.17.0.1

running() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

case "${1:-status}" in
  start)
    running && { echo "터널 이미 실행 중 (PID $(cat "$PIDF"))"; exit 0; }
    [ -s "$HOSTF" ] || { echo "[중단] $HOSTF 에 EC2 주소가 없습니다"; exit 1; }
    nohup setsid bash "$0" loop > "$LOGS/tunnel.log" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    sleep 8
    tail -n 3 "$LOGS/tunnel.log"
    running && echo "터널 실행 중 (PID $(cat "$PIDF")) — 기록 $LOGS/tunnel.log"
    ;;
  loop)
    while :; do
      echo "[$(date '+%m-%d %H:%M:%S')] 연결"
      ssh -N -i "$KEY" -o BatchMode=yes -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DIR/known_hosts" \
        -R $BIND:8031:127.0.0.1:18041 -R $BIND:8032:127.0.0.1:18042 \
        "ubuntu@$(tr -d '[:space:]' < "$HOSTF")"
      echo "[$(date '+%m-%d %H:%M:%S')] 끊김(rc=$?) — 15초 뒤 다시"
      sleep 15
    done
    ;;
  stop)
    if running; then
      PID=$(cat "$PIDF")
      kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID"
      echo "터널 중지"
    else
      echo "터널이 실행 중이 아닙니다"
    fi
    rm -f "$PIDF"
    ;;
  status)
    running && echo "터널 실행 중 (PID $(cat "$PIDF"))" || echo "터널 꺼짐"
    [ -f "$LOGS/tunnel.log" ] && tail -n 5 "$LOGS/tunnel.log"
    ;;
  *) echo "사용법: bash tunnel.sh start|stop|status"; exit 2 ;;
esac
