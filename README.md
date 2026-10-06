# wfg-server

Wildfire Guard 백엔드 — API 서버, 확산 예측(ELMFIRE), 평가 하네스.

요구사항 분석서(2026-10-01) 기준: FastAPI · PostGIS · ELMFIRE 1.1 · 1시간 간격 누적 확산 범위 P1~P8.

## 구조

| 경로 | 역할 |
|---|---|
| `app/` | FastAPI (Router / Service / Adapter). 웹 요청 처리 |
| `spread/` | 확산 예측 패키지. **FastAPI에 의존하지 않음** — 서버와 평가가 같은 코드를 호출 |
| `evaluation/` | 평가 하네스 (시나리오 S0~S6, 지표, 데이터셋 리더) |
| `migrations/` | Alembic DB 마이그레이션 |
| `docker/worker/` | ELMFIRE + `spread` 워커 이미지 |
| `tests/unit/` | CI L1 (모든 push) |
| `tests/integration/` | CI L2 (ELMFIRE 실제 실행, `-m integration`) |

## 개발 환경

Python 3.12, [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras          # 의존성 설치 (.venv)
cp .env.example .env          # 경로·키 설정
uv run pytest                 # 단위 테스트
uv run ruff check . && uv run ruff format --check .
uv run uvicorn app.main:app --reload   # http://localhost:8000/health
docker compose up -d db       # PostGIS
uv run alembic upgrade head
```

## AI Hub 데이터

```bash
# AIHUB_ROOT = …/38.산불 확산 위험 대응방안 추론 데이터/3.개방데이터/1.데이터
uv run python -m evaluation.datasets.aihub list --root "$AIHUB_ROOT" > cases.csv
uv run python -m evaluation.datasets.aihub extract HD20250407 --root "$AIHUB_ROOT" --out cases/
```

## ELMFIRE 워커 이미지

국내 지형·기상·임상도를 이용한 의성군 재현 사례와 단계별 실행 방법은
[`spread/examples/uiseong_case`](spread/examples/uiseong_case/README.md)에 정리되어 있다.

ELMFIRE는 Linux 전용이다. 이 저장소에 ELMFIRE 소스를 복사하지 않고, upstream의 **고정 커밋**을 빌드한다
(`docker/worker/Dockerfile`의 `ELMFIRE_REF`). CI가 이미지를 빌드해 GHCR에 올린다.

```bash
docker build -t wfg/elmfire-base:local \
  "https://github.com/lautenberger/elmfire.git#cbf924a7bce2023105ab498fc255a8925e410646"
```

Apple Silicon에서는 `--platform linux/amd64`가 필요하며 에뮬레이션이라 느리다. 가능하면 CI가 만든 이미지를 받아 쓴다.

주의: ELMFIRE의 Python 환경은 **GDAL < 3.11 고정**이다 (3.11부터 `gdal_translate` 등 기존 명령어 이름이 바뀌어 ELMFIRE가 실패함).
워커 이미지에서는 pip로 GDAL을 따로 설치하지 않는다.

## 커밋하면 안 되는 것 (public 저장소)

- **AI Hub 원천·라벨 데이터** (재배포 제한) — 로컬 경로(`AIHUB_ROOT`)에서만 읽는다
- 표준매뉴얼 등 정부 내부 열람 자료
- API 키 등 비밀값 (`.env`)
- DEM·임상도·실행 출력 래스터 (`data/`, `runs/`, `results/`, `cases/`는 `.gitignore`)

## 라이선스 고지

ELMFIRE는 CloudFire, Inc.의 **GNU AGPL v3.0 + Commons Clause** 라이선스다. 학술 사용은 가능하나
ELMFIRE의 기능에 가치를 두는 제품·서비스를 **판매**하려면 별도 상용 라이선스가 필요하다
(https://github.com/lautenberger/elmfire/blob/main/COMMERCIAL_LICENSE.md). 이 저장소는 ELMFIRE를 수정하지 않고 별도 실행 파일로 호출한다.
