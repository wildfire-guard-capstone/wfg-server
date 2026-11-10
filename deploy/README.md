# EC2 배포

API 서버 컨테이너(`wfg-api`)와 ELMFIRE worker 컨테이너(`wfg-worker`)를 EC2에 올린다. DB 컨테이너(`wfg-db`)는 따로 떠 있고 이 배포가 건드리지 않는다.

```
EC2 /home/ubuntu/wfg/
├── compose.yml   ← deploy/compose.ec2.yml (배포 때마다 복사됨)
├── .env          ← API_IMAGE · WORKER_IMAGE=…:<커밋> (배포가 씀, 지금 떠 있는 버전)
├── db.env        ← POSTGRES_USER · POSTGRES_PASSWORD · POSTGRES_DB (wfg-db와 같이 씀, 비공개)
├── api.env       ← API 설정(없으면 배포가 빈 파일로 만듦, 덮어쓰지 않음) — 「GPU 서버 연결」
├── data/base/    ← 바탕 데이터 적재 파일 6개 (컨테이너 /data/base, 읽기 전용)
└── deck/elmfire_deck/  ← ELMFIRE 덱(릴리스 deck-20261110) — api · worker 둘 다 /deck에 읽기 전용
```

- 앱은 `db.env`의 값과 `DB_HOST=wfg-db`로 DB 접속 주소를 만든다(`app/core/config.py`). 비밀번호는 `db.env`에만 있다
- API는 EC2 안에서만 열려 있다(`127.0.0.1:8000`). 보안 그룹 인바운드는 SSH(22)만

## 자동 배포

`main`에 머지되면 `.github/workflows/deploy.yml`이 돈다(서버 파일이 바뀐 경우만, Actions 화면에서 수동 실행도 됨).

```
lint · 테스트 → 이미지 빌드(x86): wfg-api · wfg-worker(elmfire-image.yml) → GHCR → EC2 SSH
→ compose.yml 복사 → 두 이미지 받기 → 재시작 → api /health · worker /health 확인
```

- **마이그레이션과 바탕 데이터 적재는 하지 않는다.** 아래 명령으로 직접 한다
- 레포 시크릿: `EC2_HOST`(EC2 주소), `EC2_SSH_KEY`(배포 전용 키 `github-actions-deploy@wfg-server`의 개인 키)
- 이미지는 배포 job의 임시 토큰으로 받고 바로 로그아웃한다(패키지를 공개로 바꾸지 않아도 됨)

## 직접 하는 일 (EC2에서)

```bash
ssh wfg-ec2                       # ~/.ssh/config 별명
cd /home/ubuntu/wfg

docker exec wfg-api alembic current                        # 마이그레이션 상태
docker exec wfg-api alembic upgrade head                   # 테이블 구조를 바꾼 PR을 머지한 뒤
docker exec wfg-api python -m app.db.seed.base             # 바탕 데이터: 대상 DB · 할 일만 확인
docker exec wfg-api python -m app.db.seed.base --yes       # 바탕 데이터: 모두 지우고 다시 넣기

docker compose logs --tail 100 api                         # 로그(worker도 같은 방식)
docker compose up -d api                                   # 같은 버전으로 다시 켜기
```

바탕 데이터 파일을 새로 만들었으면 먼저 복사한다(내 컴퓨터에서):

```bash
rsync -az "<capston-data>/data/지도자원/적재/" wfg-ec2:/home/ubuntu/wfg/data/base/
```

API를 내 컴퓨터에서 보려면 터널: `ssh -N -L 8000:127.0.0.1:8000 wfg-ec2` → http://localhost:8000/health

## ELMFIRE 예측 (worker, 구조 B)

ELMFIRE는 worker 컨테이너에서만 돈다. API는 예측 요청 때 기상 입력(관측소 격자 · 발화점 칸 9시간)을
만들어 worker에 보내고, worker가 P1 ~ P8을 돌려준다(`app/predict/worker_server.py`, docs/ELMFIRE-기상.md).

```
wfg-api ── 기상 입력(npz) ──▶ http://wfg-worker:8100/run ── ELMFIRE(약 20초 · 1.1 GB) ──▶ P1 ~ P8
           (wfg-net 안에서만, worker 포트는 밖에 열지 않음)
```

- worker는 늘 떠 있고 한 번에 1회씩 돌린다(뒤 요청은 기다림). 메모리 상한 1.3 GB(+ 스왑 약 0.7 GB),
  ELMFIRE 1회 5분 제한. 넘치면 그 예측만 실패하고 api · db는 그대로
- 덱은 처음 한 번 받아 둔다: 릴리스 `deck-20261110`의 `elmfire_deck_a.zip`을 `deck/`에 풀기
- **켜기**(`api.env`, 키는 화면에 찍지 않는다):

```bash
cat >> /home/ubuntu/wfg/api.env <<'ENV'
PREDICTOR=elmfire
PREDICTOR_WEATHER_FIELD=stations   # 실시간(관측소 매분자료 + 단기예보). 시연은 deck + WEATHER=asos
KMA_API_KEY=…                      # API허브(매분자료 · 지상관측)
DATA_GO_KR_API_KEY=…               # 공공데이터포털(단기예보)
ENV
cd /home/ubuntu/wfg && docker compose up -d api
```

- 확인: `docker compose logs --tail 20 worker` · `docker exec wfg-api python -c "import urllib.request as u; print(u.urlopen('http://wfg-worker:8100/health').read())"`
- 끄기: `api.env`에서 `PREDICTOR` 줄을 지우고 `docker compose up -d api`(가짜 예측 synthetic으로 돌아감)
- 관측소 기상을 못 받으면(3곳 미만) 예측 요청이 503 `WEATHER_UNAVAILABLE` — 다른 기상으로 대신하지 않는다

## GPU 서버 연결 (제안서 · AI 어시스턴트를 EXAONE으로)

학교 GPU 서버는 밖에서 들어오는 접속을 막는다. 그래서 **GPU 서버가 EC2로 SSH를 걸어** EC2 안에
포트 두 개를 만들어 둔다(역방향 터널). API 컨테이너는 그 포트로 요청하고, 요청은 터널을 타고
GPU 서버의 vLLM · 근거 서버로 간다.

```
wfg-api 컨테이너 ──▶ gpu:8031 · gpu:8032 (= EC2 docker0 172.17.0.1)
                        │  SSH 터널(GPU 서버가 걸어 둠)
                        ▼
GPU 서버 127.0.0.1:18041 vLLM(EXAONE) · 127.0.0.1:18042 근거 서버
```

처음 한 번(EC2에서):

```bash
# 1. 컨테이너가 터널 포트에 닿도록, 터널이 docker0 주소에 묶이는 것을 허용
echo 'GatewayPorts clientspecified' | sudo tee /etc/ssh/sshd_config.d/60-wfg-tunnel.conf
sudo sshd -t && sudo systemctl reload ssh

# 2. GPU 서버의 터널 전용 공개 키 등록 — 포트 전달만, 그 두 포트만
echo 'restrict,port-forwarding,permitlisten="172.17.0.1:8031",permitlisten="172.17.0.1:8032" <공개 키 한 줄>' >> ~/.ssh/authorized_keys

# 3. API 설정
cat >> /home/ubuntu/wfg/api.env <<'ENV'
PROPOSAL_WRITER=agent
ASSISTANT=exaone
LLM_URL=http://gpu:8031
EVIDENCE_URL=http://gpu:8032
ENV
cd /home/ubuntu/wfg && docker compose up -d api
```

- 보안 그룹은 그대로 22번만. 22번을 특정 IP로 좁혔다면 GPU 서버 IP를 더한다. 8031 · 8032는 열지 않는다
- GPU 서버 쪽(`deploy/gpu/`의 두 스크립트를 `~/wfg/tunnel/`에 둔다): `serve.sh start`로 vLLM · 근거 서버를 켜고 `tunnel.sh start`로 터널을 건다. GPU 서버의 8031 · 8032는 다른 계정이 써서 18041 · 18042로 띄우고, 터널이 EC2의 8031 · 8032에 잇는다. EC2 주소는 `~/wfg/tunnel/ec2_host` 한 줄 — EC2 IP가 바뀌면 고친다
- 확인(EC2에서): `curl -s http://172.17.0.1:8031/v1/models` · `docker exec wfg-api python -c "import urllib.request as u; print(u.urlopen('http://gpu:8031/v1/models').read()[:80])"`
- 터널이나 vLLM이 꺼져 있으면 예측 작업은 `PROPOSAL_FAILED`로 끝나고 예측 · 제안서 모두 저장하지 않는다. 가짜 작성기로 되돌리려면 `api.env`에서 `PROPOSAL_WRITER` 줄을 지우고 다시 켠다
- 공용 GPU라 vLLM은 쓸 때만 켠다(상시 실행은 관리자 허락 뒤)

## 처음 한 번 (끝남, 2026-11-08)

1. 보안 그룹: 인바운드 22만, 키 로그인만(비밀번호 로그인 꺼짐)
2. `wfg-db` 컨테이너 · `wfg-net` 네트워크 · `db.env` (직접 띄움)
3. 배포 전용 키를 EC2 `~/.ssh/authorized_keys`에 등록, 개인 키는 레포 시크릿 `EC2_SSH_KEY`에만
4. `data/base/`에 적재 파일 복사

## EC2 IP가 바뀌면

탄력적 IP가 없어서 인스턴스를 중지 → 시작하면 공인 IP가 바뀐다(재부팅은 그대로). 고칠 곳: 레포 시크릿 `EC2_HOST`, 내 컴퓨터 `~/.ssh/config`의 `wfg-ec2` HostName, DBeaver SSH 호스트.
