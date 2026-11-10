# EC2 배포

API 서버 컨테이너(`wfg-api`)를 EC2에 올린다. DB 컨테이너(`wfg-db`)는 따로 떠 있고 이 배포가 건드리지 않는다.

```
EC2 /home/ubuntu/wfg/
├── compose.yml   ← deploy/compose.ec2.yml (배포 때마다 복사됨)
├── .env          ← API_IMAGE=…:<커밋> (배포가 씀, 지금 떠 있는 버전)
├── db.env        ← POSTGRES_USER · POSTGRES_PASSWORD · POSTGRES_DB (wfg-db와 같이 씀, 비공개)
├── data/base/    ← 바탕 데이터 적재 파일 6개 (컨테이너 /data/base, 읽기 전용)
└── data/risk/uiseong/ ← 위험도 지역 격자 grades.tif · values.tif · meta.json (컨테이너 /data/risk, 읽기 전용)
```

- 앱은 `db.env`의 값과 `DB_HOST=wfg-db`로 DB 접속 주소를 만든다(`app/core/config.py`). 비밀번호는 `db.env`에만 있다
- API는 EC2 안에서만 열려 있다(`127.0.0.1:8000`). 보안 그룹 인바운드는 SSH(22)만

## 자동 배포

`main`에 머지되면 `.github/workflows/deploy.yml`이 돈다(서버 파일이 바뀐 경우만, Actions 화면에서 수동 실행도 됨).

```
lint · 테스트 → 이미지 빌드(x86) → GHCR(ghcr.io/wildfire-guard-capstone/wfg-api) → EC2 SSH
→ compose.yml 복사 → 이미지 받기 → 재시작 → /health 확인
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

docker compose logs --tail 100 api                         # 로그
docker compose up -d api                                   # 같은 버전으로 다시 켜기
```

바탕 데이터 파일을 새로 만들었으면 먼저 복사한다(내 컴퓨터에서):

```bash
rsync -az "<capston-data>/data/지도자원/적재/" wfg-ec2:/home/ubuntu/wfg/data/base/
```

위험도 지역 격자(지형 7 · 연료 6, 의성군)를 새로 만들었으면 같은 방법으로 복사한다. 없으면 API는 그대로 뜨고
RISK-01이 그 13개를 빼고 계산한다(이유 표시). 컨테이너가 요청마다 P5 근처 칸만 읽으니 다시 켤 필요는 없다.

```bash
# 만들기(내 컴퓨터, ELMFIRE 덱 · demo-uiseong/input 필요 — demo/uiseong/build_risk_grid.py 참고)
uv run python -m demo.uiseong.build_risk_grid --input <demo-uiseong/input> --deck <A′ 덱> \
    --tables <11/1 덱> --out <폴더>/uiseong
# 올리기(배포가 먼저 돌았으면 docker가 data/risk를 root로 만들어 두므로 주인을 ubuntu로)
ssh wfg-ec2 "mkdir -p /home/ubuntu/wfg/data/risk && sudo chown ubuntu:ubuntu /home/ubuntu/wfg/data/risk"
scp -r <폴더>/uiseong wfg-ec2:/home/ubuntu/wfg/data/risk/
```

API를 내 컴퓨터에서 보려면 터널: `ssh -N -L 8000:127.0.0.1:8000 wfg-ec2` → http://localhost:8000/health

## 처음 한 번 (끝남, 2026-11-08)

1. 보안 그룹: 인바운드 22만, 키 로그인만(비밀번호 로그인 꺼짐)
2. `wfg-db` 컨테이너 · `wfg-net` 네트워크 · `db.env` (직접 띄움)
3. 배포 전용 키를 EC2 `~/.ssh/authorized_keys`에 등록, 개인 키는 레포 시크릿 `EC2_SSH_KEY`에만
4. `data/base/`에 적재 파일 복사

## EC2 IP가 바뀌면

탄력적 IP가 없어서 인스턴스를 중지 → 시작하면 공인 IP가 바뀐다(재부팅은 그대로). 고칠 곳: 레포 시크릿 `EC2_HOST`, 내 컴퓨터 `~/.ssh/config`의 `wfg-ec2` HostName, DBeaver SSH 호스트.
