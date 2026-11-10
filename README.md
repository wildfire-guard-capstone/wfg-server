# wfg-server

Wildfire Guard 백엔드 — API 서버, 확산 예측(ELMFIRE), 평가 하네스.

요구사항 분석서(2026-10-01) 기준: FastAPI · PostGIS · ELMFIRE 1.1 · 1시간 간격 누적 확산 범위 P1~P8.

## 구조

| 경로 | 역할 |
|---|---|
| `app/` | FastAPI (Router / Service / Adapter). 웹 요청 처리 |
| `spread/` | 확산 예측 패키지. **FastAPI에 의존하지 않음** — 서버와 평가가 같은 코드를 호출 |
| `rules/` | 규칙 엔진(대응단계 · 지휘권자 등 판정). 순수 함수만, **FastAPI · DB · `spread`에 의존하지 않음** |
| `agent/` | 대응 제안 생성: 규칙 엔진 판정 → 근거 검색(RAG) → EXAONE → 검사 → 근거 표기 · 조립 |
| `assistant/` | AI 어시스턴트(질의응답 · 상황 정정): 입력 구분 → 도구 고르기 → 도구 실행 → EXAONE 답 → 출처 검사 / 정정 확인안 |
| `demo/uiseong/` | 의성 산불(2025-03-22) 데모: 입력 → 확산 예측 → 규칙 판정 → LLM 대응 생성 → 출력, 뷰어 · 검증 |
| `docs/` | 구조서(`의성-데모-구조서.md`: 단계별 파일 · 함수 · 출력 · 디버깅 안내) |
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

## 대응 제안 생성(`agent/`)과 의성 데모(`demo/uiseong/`)

규칙 엔진은 규칙으로 판단할 수 있는 것(대응단계 · 지휘권자 · 위험구역 마을 · 도달 시각 · 면적 · 자원 · 바람 · 일몰)만 판정해 넘기고,
**무엇을 할지(대응 제안)는 LLM(EXAONE 4.5 33B)이 표준매뉴얼 · 법령을 검색해 그 근거로 쓴다.** 코드가 문장을 미리 정해 두지 않는다.

```
입력(scenario.json · 상황도 화선 · 지도 · ELMFIRE 입력)
  → 1 확산 예측   demo/uiseong/step1_spread.py → spread/perimeter_run.py   회차 화선에서 배율 보정 · 8시간 예측 → P0~P8
  → 2 규칙 판정   demo/uiseong/step2_rules.py  → rules/                    판정 16묶음 + 항목 12개(S1~S6 · E1~E6)
  → 3 LLM 대응 생성 demo/uiseong/step3_agent.py → agent/                  항목마다 아래 순서
       situation.py · guides.py  상황 깃발(불이 큼 · 작음 · 단계 오름 · 밤 · 도지사 …) → 항목별 작성 지침(쓸 것 · 덧붙일 제안 · 쓰지 않을 것)
       judgment.py  판정만 고름(문단 자리 · 고정 문장 없음)
       situate.py   판정 값 상황 문장(S1 · S2 · S3)은 코드가 facts로 조립 — LLM은 그 뒤의 할 일 문장만
       retrieve.py  질의 3개로 근거 검색 + 규칙 엔진이 판정에 쓴 근거
       prompt.py    지시문 · 항목 범위 · 읽는 사람의 권한 · 판정 · 근거 발췌 → json_schema(evidence는 준 청크 ID만, quote = 근거 문장)
       check.py     quote가 단 근거에서 왔는지 · 판정 값 · 권한 · 형식 검사 → 1회 다시 → 걸린 제안만 뺌
       cite.py      청크 ID → 근거 표기 [p.79, 시행령 제21조①·②]
  → 출력          runs/<이름>/round_k/01_spread · 02_rules · 03_rag · 04_llm(프롬프트 전문 · 답 원문) · 05_output, viewer.html
```

실행에는 GPU 서버의 세 서비스가 필요하다: ELMFIRE 1.1(CPU), 근거 검색 서버(`127.0.0.1:8032`, KURE-v1 + FAISS + PostgreSQL —
`rag-pipeline`, 이 저장소 밖), vLLM(`127.0.0.1:8031`, EXAONE 4.5 33B AWQ). 입력 데이터(지도 자료 · ELMFIRE 입력)와 근거 코퍼스는
공개 저장소에 넣지 않는다.

```bash
# 서버에서(입력 = $DEMO_HOME/input)
OUT=~/wfg/demo/runs/<이름> bash demo/uiseong/run_demo.sh                                   # 전부
OUT=~/wfg/demo/runs/<이름> bash demo/uiseong/run_demo.sh --reuse-spread <앞 결과> --items S1,E2   # 규칙 · 제안만, 항목 몇 개만
python -m demo.uiseong.verify --runs <결과> --ref <reference>                              # 근거 뒷받침 · 시나리오 목표 · 실제 기록 대비
# 로컬
uv run python -m demo.uiseong.build_viewer --runs <결과> --data <input> --out viewer.html
uv run pytest -q tests/unit/test_agent_check.py tests/unit/test_agent_generate.py tests/unit/test_demo_calibration.py
```

2026-11-08 결과(4회차: 12:50 · 13:10 · 13:45 · 14:20, `runs/tune10`): 46개 항목 모두 제안을 냈고 다룰 주제 101개 중 99개에 문단이 붙었다(문단 평균 61 ~ 74자).
시나리오 v3 회차 1 목표 대비 0.759(같은 주제 문단 0.774 · 분량 1.0), 제안의 조치가 단 근거 원문에 있는 비율 0.79 ~ 0.94, 회차 뒤 실제 조치(백서)를
미리 권한 비율 0.594(판정은 같은 EXAONE이라 후할 수 있음). 남은 약점: 도지사 회차에서 지시를 모두 군수에게 돌리고, 일부 문단에 근거에 없는 세부가 섞인다.
같은 날 밤 항목별 작성 지침을 넣은 `runs/tune11`: 근거 뒷받침 0.90 ~ 0.93(도지사 회차 0.79 → 0.93), 목표 대비 0.778, 다룰 주제 101개 중 100개.
대신 판정 값을 근거의 기준값으로 바꿔 쓰는 오류(1단계 지상 범위 「경상북도 100%」 · 「최소 20동」)가 남아, S1 · S2 · S3의 상황 문장을 코드가
판정으로 조립하고 LLM은 할 일 문장만 쓰게 했다(`agent/situate.py`). 최종 `runs/tune15`: 근거 뒷받침 0.95 · 0.90 · 0.90 · 0.90, 목표 대비 0.815,
백서 실제 기록 0.625, 실패 항목 0, S1~S3 판정 값 오류 0(tune11은 6건).
화면(뷰어)은 제안 문장만 보이고 근거는 마우스를 올리면 보인다.

자세한 구조 · 데이터 형식 · 디버깅은 [`docs/의성-데모-구조서.md`](docs/의성-데모-구조서.md), 회차 · 보정 · 조정 기록은
[`demo/uiseong/README.md`](demo/uiseong/README.md).

## AI 어시스턴트 — 질의응답 · 상황 정정(`assistant/`, API AI-01 · AI-02 · COR-01 · COR-02 · PROP-05)

통합지휘권자의 입력 하나를 **질문**(답을 `qa_messages`에 저장) · **상황 정정**(확인안만, 확인하면 `situation_corrections` +
`incidents.situation` 저장 → 같은 예측으로 제안서 새 버전) · **처리 불가**(대응단계 · 기상 등 정정할 수 없는 말) 중 하나로 처리한다.
답의 문장마다 출처(근거 문서 `[k]` 또는 상황 값 경로)가 있어야 내보내고, 숫자 · 시각 · 마을 이름은 그 출처에 있어야 한다.

```bash
uv run python -m assistant.cli --round <데모 회차 폴더> "헬기 몇 대 투입됐어?" "괴산리 대피 완료"          # LLM 없이(규칙만)
python -m assistant.cli --round <회차> --llm http://127.0.0.1:<vLLM> --evidence http://127.0.0.1:<근거> \
    --batch assistant/eval/questions.json --out out.json                                       # GPU 서버, EXAONE
uv run pytest -q tests/unit/test_assistant_*.py
```

API 서버 설정: `ASSISTANT=rules`(기본, LLM 없음) · `exaone`(+ `LLM_URL` · `LLM_MODEL` · `EVIDENCE_URL`), `CORRECTION_TTL_S`(확인안 10분).
구조 · 도구 16개 · 프롬프트 · 검사 · 정정 규칙 · 미결 사항은 [`docs/AI-어시스턴트-구조서.md`](docs/AI-어시스턴트-구조서.md).

## 결과 조회 API(임시 · 김지오 전용, `/api`)

> **임시**: 주 API는 DB를 쓰는 `/api/v1`(`app/routers`, API 명세서 v0.4)이다. 이 결과 조회 API는 그것이 화면에
> 붙을 때까지만 쓰고, 코드는 `app/demo_results/` 한 폴더에 모여 있다(지우는 법은 그 폴더 `__init__.py`).

GPU 서버에서 만든 데모 결과를 웹 화면(wfg-client)이 읽을 수 있게 내주는 읽기 전용 API다. DB 대신
결과 묶음 폴더(JSON)를 읽는다(`app/demo_results/results.py`).
응답 형식 · 열거형 값은 DB 설계(`app/db/models`)를 따른다. 문서: 서버를 띄운 뒤 `/docs`.

| 메서드 · 경로 | 내용 | 권한 |
|---|---|---|
| `POST /api/auth/login` | `{login_id, password}` → `access_token`(Bearer) · `user` | — |
| `GET /api/auth/me` | 로그인한 사용자 | 로그인 |
| `GET /api/incidents` · `/{id}` | 산불 목록 · 상세(발화점, 현재 대응단계, 최신 버전) | 통합지휘권자 · 열람자 · 상황 보고자 |
| `GET /api/incidents/{id}/perimeters` | 실측 화선 v0(발화점) ~ vN, `?geometry=false`면 좌표 없이 | 통합지휘권자 · 열람자 · 상황 보고자 |
| `GET /api/incidents/{id}/predictions` · `/{n}` | 확산 예측 목록 · 상세(P1~P8 범위, 시간별 바람, 보정 배율, 뒤 화선 비교) | 통합지휘권자 · 열람자 |
| `GET /api/incidents/{id}/proposals` · `/{버전 또는 latest}` | 제안서 목록 · 상세(항목별 판정 · 제안 문장 · 근거 원문) | 통합지휘권자 |

좌표는 GeoJSON(EPSG:4326), 시각은 ISO 8601(+09:00). 로그인 실패 5회면 그 아이디를 잠근다(서버 재시작 시 풀림).
토큰을 받은 뒤 계정을 지우면 그 토큰도 바로 막힌다.

```bash
# 1) 결과 묶음 만들기(로컬 또는 GPU 서버) — 프롬프트 · 실행 기록은 빼고 화면에 필요한 것만, 약 1 MB
uv run python -m demo.uiseong.export_bundle --run <결과 폴더(runs/tune10)> --input <demo-uiseong/input> --out <묶음 폴더>
# 2) 계정 만들기(비밀번호는 화면에서 묻는다, 8자 이상)
uv run python -m app.demo_results.accounts add --file <accounts.json> --login cmd01 --role COMMANDER --name 홍길동 --org 의성군
# 3) .env에 RESULTS_DIR · ACCOUNTS_FILE · AUTH_SECRET(32자 이상 무작위) 넣고 실행
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

묶음 폴더와 계정 파일에는 표준매뉴얼 원문 · 비밀번호 해시가 들어 있으므로 저장소에 넣지 않는다.

## AI Hub 데이터

```bash
# AIHUB_ROOT = …/38.산불 확산 위험 대응방안 추론 데이터/3.개방데이터/1.데이터
uv run python -m evaluation.datasets.aihub list --root "$AIHUB_ROOT" > cases.csv
uv run python -m evaluation.datasets.aihub extract HD20250407 --root "$AIHUB_ROOT" --out cases/
```

## ELMFIRE 워커 이미지

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
- 표준매뉴얼 등 정부 내부 열람 자료(근거 코퍼스 · 교정본 · 청크)
- 의성 데모 입력(지도 자료 · ELMFIRE 입력)과 실행 결과(`runs/` — 프롬프트에 매뉴얼 원문이 들어 있음)
- 서버 주소 · 계정 · 접속 키
- API 키 등 비밀값 (`.env`)
- DEM·임상도·실행 출력 래스터 (`data/`, `runs/`, `results/`, `cases/`는 `.gitignore`)

## 라이선스 고지

ELMFIRE는 CloudFire, Inc.의 **GNU AGPL v3.0 + Commons Clause** 라이선스다. 학술 사용은 가능하나
ELMFIRE의 기능에 가치를 두는 제품·서비스를 **판매**하려면 별도 상용 라이선스가 필요하다
(https://github.com/lautenberger/elmfire/blob/main/COMMERCIAL_LICENSE.md). 이 저장소는 ELMFIRE를 수정하지 않고 별도 실행 파일로 호출한다.
