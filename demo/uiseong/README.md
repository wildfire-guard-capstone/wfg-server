# 의성 데모: 확산 예측부터 AI 제안 생성까지

2025-03-22 의성 산불(안평면 괴산리)을 상황도 화선 보고 4번(12:50 · 13:10 · 13:45 · 14:20)마다
**ELMFIRE 예측 → 규칙 엔진 → 에이전트(EXAONE)** 순서로 실제로 돌려 회차별 제안서를 만든다.
결과는 한 장짜리 뷰어 HTML로 본다.

```
상황도 k보 화선 ─┬─ 보정: 직전 상태 → k보 화선 면적이 맞는 확산속도 배율(ELMFIRE adj)
                 └─ 예측: k보 화선에서 8시간 → P0 ~ P8          (spread/perimeter_run.py)
지도 자료 + 현장 입력(상황 보고) + P0 ~ P8 → rules.Inputs       (demo/uiseong/data.py · rounds.py)
  → build_llm_input → 엔진 JSON 16묶음 + 12개 항목 LLM 입력      (rules/)
  → 항목별: 근거 청크(앵커 ID + 보충 검색) + EXAONE → 검사 → 조립 (agent/)
  → round_k.json → build_viewer.py → viewer.html
```

## 회차(11/7 사용자 결정)

| 회차 | 버전 | 기준 화선 | 읽는 사람 | 보정 구간 |
|---|---|---|---|---|
| 1 | v1.1 | 12:50 상황도 1보 | 의성군수 | 11:24 발화점 → 12:50 1보 |
| 2 | v2.1 | 13:10 2보 | 의성군수 | 1보 → 2보 |
| 3 | v3.1 | 13:45 3보 | 의성군수 | 2보 → 3보 |
| 4 | v4.1 | 14:20 4보 | 경상북도지사 | 3보 → 4보 |

- 화선 = 백서 부록 2 상황도를 좌표화한 결과(11/6, `agent-spec/의성 시나리오(20261106)/_빌드/v2_공간계산`)
- 투입 수 · 기상(0시) = 백서 부록 3 상황 보고 1 ~ 4보, 대피 문자 · 대피명령 = 시나리오 v1 기록(`rounds.py`)
- 안계면 양곡리(14:39) · 금성면 청로리(13:57) 산불은 예측 시각에 알 수 없어 넣지 않았다

## 실행 위치와 준비(학교 GPU 서버, A6000)

| 무엇 | 어디 | 비고 |
|---|---|---|
| ELMFIRE 1.1 | `~/wfg/elmfire/bin/elmfire` | `elmfire-linux` 소스를 conda 환경 `wfg-elm`(gfortran · openmpi · GDAL 3.10)으로 `make_gnu.sh fast` 빌드. CPU로 돈다 |
| ELMFIRE 입력 | `~/wfg/demo/deck` → `~/wfg/elmfire/deck` | 「ELMFIRE 입력」(11/1) 그대로. 회차마다 phi · adj · 시간 창만 바꾼 실행 폴더를 만든다 |
| 데모 데이터 | `~/wfg/demo/data` | `prepare_data.py` 결과(지도 GeoJSON · SGIS 경계 · 화선). 공개 저장소에 넣지 않는다 |
| 근거 검색 | 127.0.0.1:8032 | `rag-pipeline/serve_evidence.py`(KURE-v1 + FAISS + PostgreSQL). 표준매뉴얼 17쪽(SM-pNNN) 추가 |
| LLM | 127.0.0.1:8031 | EXAONE 4.0 32B AWQ, vLLM 0.10.1.1(`rag-pipeline/deploy/server/llm.sh start`) |

```bash
# 1) 서비스 켜기(공용 서버라 데모가 끝나면 끈다)
bash ~/wfg/rag-pipeline/deploy/server/db.sh start
nohup setsid ~/miniforge3/envs/wfg/bin/python ~/wfg/rag-pipeline/serve_evidence.py --device cuda > ~/wfg/logs/evidence.log 2>&1 &
bash ~/wfg/rag-pipeline/deploy/server/llm.sh start
# 2) 실행(약 n분) → ~/wfg/demo/runs/<시각>/round_k.json
cd ~/wfg/demo/wfg-server && bash demo/uiseong/run_demo.sh
# 3) 끄기
bash ~/wfg/rag-pipeline/deploy/server/llm.sh stop; kill $(cat ~/wfg/run-evidence.pid); bash ~/wfg/rag-pipeline/deploy/server/db.sh stop
```

뷰어(로컬): 결과 폴더를 내려받아
`uv run python -m demo.uiseong.build_viewer --runs <결과 폴더> --data <데모 데이터> --out viewer.html`.

## 에이전트 규칙(agent/)

- 호출 1회 = 지시문 + `common` + 항목(조립기 블록은 자리만) + 근거 청크 본문. 출력은 action마다 `{action, category, text}`
- 근거 = `rule_basis ∪ anchor_basis` 청크를 ID로 가져오고, 항목 이름 + 주제로 보충 검색 2개
- 검사: 입력에 없는 숫자 · 시각 · 한글 수사 · 이름, anchors 밖 근거 표기, 근거 없는 할 일 문장, 「등」, 항목별 금칙
  (S3 추가 대수, S4 운항 가능 단정, E4 배정 인원 · 경로, E5 문안)
- 실패하면 문제 목록을 주고 1회 다시. 그래도 실패하면 그 항목만 「문장 생성 실패」(판정 · 목록 · 표는 남김)
- 추론 모드는 S2 · S4 · S5만, 입력이 직전 회차와 같은 항목은 직전 문단을 다시 쓴다(11/7)

## 내 기본값(확인 필요)

- 0시 풍속 · 풍향 = 상황 보고 값, 1 ~ 8시간 = ELMFIRE 입력 기상(화선 중심, 시간 선형 보간)
- 대피소 tier: 경로당 = village, 그 밖 = hub. 같은 이름 대피소는 작은 수용 인원 하나
- 헬기 기지 관할(`data.COVERAGE`)은 가정값. 기지별 대기 · 투입 대수는 자료가 없어 비움
- 전통사찰 자료가 없어 `temples = ()`(S2 문화유산 문단 · S6 경찰 행이 빠짐)
- 고속도로 나들목 자료가 없어 구간 이름을 리 이름으로 씀(엔진이 unknown으로 표시)
