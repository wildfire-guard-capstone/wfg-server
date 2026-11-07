# rules — 규칙 엔진 · 제안 입력 조립

예측 범위(P0 ~ P8)와 지도 · 현장 데이터를 받아 **엔진 JSON**(사실 + 판정)을 만들고, 그것으로 **12개 항목의 LLM 입력**을 조립한다.
순수 함수만 둔다. FastAPI · DB 세션 · `spread`를 import하지 않는다 — app이 데이터를 읽어 이 함수들을 부르고 결과를 저장한다.

```python
from rules.llm_input import build_llm_input

out = build_llm_input(inputs, incident)  # inputs: rules.context.Inputs
out["bundles"]  # 엔진 JSON(16묶음)       → proposals.context
out["common"]  # 모든 항목 공통 값        → LLM 입력 common
out["items"]  # 12개 항목 입력 + 섹션    → proposal_items.detail
```

## 1. 큰 그림: 엔진 · 조립기 · 에이전트

| 층 | 역할 | 결과 | 판정 |
|---|---|---|---|
| `core/` · `context.py` · `bundles/` | **엔진**: 무엇이 사실인가. 항목을 모른다 | 엔진 JSON(주제별 고정 모양 16묶음) | ✅ 여기서만 |
| `items/` · `sections/` | **조립기**: 항목마다 LLM이 쓸 자리와 쓸 값. 프롬프트의 데이터 부분 | 항목 입력 + 섹션(문단 자리 · 조립기가 채우는 목록 · 표) | ❌ 고르기 · 이름 바꾸기 · 틀 채우기만 |
| 에이전트(`rules` 밖) | 지시문 · 근거 청크(`rule_basis` · `anchor_basis`)를 붙여 LLM 호출, 문장 검사 | 제안 문장 | — |

- LLM 문장이 이상하면 `items` · `sections` · 지시문을 고친다. 엔진 JSON은 그대로 둔다
- 사실이나 판정이 틀렸을 때만 엔진(`core` · `bundles`)을 고친다

## 2. 흐름

```
입력(Inputs: 범위 · 마을 · 시설 · 기상 · 현장 입력 · 직전 제안서)
 → context.build_context()   여러 묶음이 같이 쓰는 계산을 한 번 (core 함수를 부름)
 → bundles.build_bundles()   엔진 JSON 16묶음 (주제별 사실 + 판정)
 → items/*_input()           항목 입력 12개 (필요한 묶음만 골라 씀)
 → sections/*_sections()     항목마다 sections(문단 자리) · do_not_state · anchor_basis
 → llm_input.build_llm_input()  bundles + common + items
```

- **한 방향**이다. 아래 층이 위 층을 부르지 않는다
- 한 줄이 아니라 **퍼졌다가 모인다**: context 하나 → 묶음 16개 → 항목은 필요한 묶음만(S1 = `stage` · `area` · `commander`, E1 = `villages`)
- 묶음 하나만 쓰는 계산(넘은 면적 기준 · 대피소 안전 등)은 그 묶음 안에서 `core`를 직접 부른다
- 예외: S1 · E1 섹션은 항목 입력에 더해 묶음도 받는다(`llm_input.ITEMS`의 세 번째 값)

## 3. 층별

### `core/` — 규칙 함수

값을 받아 재고 판정만 한다. 항목도 JSON도 모른다.

| 파일 | 하는 일 |
|---|---|
| `area.py` | P0 ~ P8 면적(R-15), 처음 넘는 면적 기준 |
| `stage.py` | 요소별 단계 · 대응단계(J3 · J4), 추세(P5 vs 현재), 직전 대비 변화 |
| `commander.py` | 지휘권자(J6) · 직함 |
| `facility.py` | P5 안 주택 · 국가유산 수(J5) |
| `reach.py` | 닿는지(`touches`), 처음 닿는 P(`first_p`) |
| `villages.py` | P8 안 마을과 처음 닿는 P, 현장 입력 모양 |
| `regions.py` | 걸친 시 · 도 · 시 · 군 · 구 |
| `sun.py` | 일출 · 일몰 · 밤(J2) |
| `weather.py` | 현재 풍속 · 0 ~ 5시간 평균풍속 |
| `direction.py` | 방위각 · 16방위 · 8방위 |
| `convert.py` | 시각 · 숫자 표기, 구간(J1 `zone_of`) |
| `places.py` | 시설 · 기지 · 도로 · 송전선 · 투입 자료 모양(`Place` · `Base` · `RoadPiece` · `PowerLine` · `Deployed`) |

### `context.py` — 입력과 공통 계산

- `Inputs`: 엔진이 받는 것 전부(범위 P0 ~ P8, 시간별 풍향 · 풍속, 주택 · 시설 · 기지 · 도로, 행정 경계, 현장 입력, 직전 단계 · 직전 읍면 단계)
- `build_context()`: 면적 → 풍속 요약 → 시설 수 → 현재 · P5 대응단계 → 추세 · 변화 → 걸친 지역 → 지휘권자 → P8 안 마을 → 일출 · 일몰 (순서와 근거는 함수 docstring)
- **빠진 자료는 메우지 않고 오류를 낸다**: P8이 걸친 시 · 군 · 구에 리 자료가 없을 때, 0 ~ 5시간 풍속이 빠졌을 때(`weather` 묶음은 0 ~ 8시간)

### `bundles/` — 엔진 JSON

주제 하나 = 파일 하나. `build_bundles()`가 모두 만들고 `proposals.context`에 그대로 저장된다.

| 묶음 | 내용 | 쓰는 항목 |
|---|---|---|
| `time` | 기준 · 생성 시각, Pn 시각, 일출 · 일몰, 지금 밤인가 | S2 · S4 · E2 · E3 · common |
| `area` | P0 ~ P8 면적, 넘은 기준 | S1 |
| `stage` | 현재 · P5 대응단계와 정한 요소, 추세, 직전 대비 | S1 · S3 · common |
| `commander` | 지금 · 곧 지휘권자와 이유 | S1 · common |
| `villages` | 구간별 마을(이미 닿음 · 5시간 · 8시간), 시각 칸, 인구, 현장 입력 | S2 · E1 · E2 · 요청 표 |
| `vulnerable` | 8시간 안 취약시설 | S2 · E3 · 요청 표 |
| `protected` | 송전선 · 변전소 · 전통사찰 · 국가유산(+ 가까운 경찰서) | S2 · 요청 표 |
| `houses` | 5시간 안 주택(리별) | S2 · 요청 표 |
| `shelters` | 마을별 대피소 후보(J8) | E4 |
| `roads` | 5시간 안 큰 도로 구간, 가까운 경찰서 | E4 · 요청 표 |
| `alerts` | 읍면 Go · Set(J9), 직전 대비, 문자 기록 | E5 · 요청 표 |
| `mobilization` | 단계별 동원 범위(J7), 범위 안 기지(대기 · 투입) | S3 · 요청 표 |
| `resources` | 투입 수(상황 보고), 기지별 투입, 인력을 보낸 기관 | S3 · S5 |
| `weather` | 현재 · 시간별 · 최대 풍속 | S4 · S5 |
| `spread` | 주 확산 방향, 다음에 닿는 마을 | S5 |
| `agencies` | 관할 시군 · 시도, 시군구별 관할 소방서 · 경찰서, 응급의료기관 | S6 · 요청 표 |

`time` 묶음은 `timing.py`에 있고, `common.py`는 묶음들이 같이 쓰는 도우미(거리 · 가까운 곳 · 마을 이름 · 시각 칸)다. 각 묶음 함수의 docstring에 계산 순서가 번호로 적혀 있다.

**JSON 규칙**

- 키는 늘 있다. 없으면 `[]` · `0` · `false` · `null`
- 모름 = `null` + `unknown: [{field, reason}]`. 0과 모름을 나눈다
- 묶음마다 `basis`(판정 근거 청크 ID)와 `unknown`
- 시각 칸(`timing`): `first_p` · `zone`(burning · p5 · p8) · `from` · `until`(P0 = 「지금」) · `passed` · `night`
- **할 일 이름을 붙이지 않는다**: 협의 · 인계 · 요청 · 대피명령 · 시한 · 「누구에게」는 LLM 몫

### `items/` — 항목 입력

판정하지 않는다. 묶음에서 고르고, 이름을 바꾸고, 틀을 채운다.

| 칸 | 뜻 |
|---|---|
| `id` · `name` | S1 ~ S6 · E1 ~ E6 |
| `status` | 상황 이름(있는 사실의 이름. 예: `escalation_expected` · `risk_zone`) |
| `finding` | 한 줄 요약(틀 채우기) |
| `facts` | LLM이 쓸 사실(묶음에서 고른 값) |
| `targets` | 지도에서 강조할 대상 id |
| `withheld` | 비운 칸 `{field, reason}` → 섹션의 `do_not_state` |
| `rule_basis` | 판정 근거 청크 ID |

만들 것이 없으면 `None`을 돌려주고 그 항목은 빠진다(예: P8 안에 마을이 없으면 E1 · E5 없음).
`requests.py`는 S6 · E6 **요청 표 틀**이다: 묶음의 대상 · 관서 이름(사실)을 매뉴얼 기관별 임무 문장으로 짝짓는다.

| 항목 | 이름 | 쓰는 묶음 |
|---|---|---|
| S1 | 대응단계 판단 · 격상 검토 · 지휘권 인계 | `stage` · `area` · `commander` |
| S2 | 진화 우선지역 · 보호대상 · 시설 보호 | `villages` · `vulnerable` · `houses` · `protected` · `time` |
| S3 | 진화자원 현황 · 동원 범위 | `resources` · `mobilization` · `stage` |
| S4 | 진화 방식 · 시간대 전략 | `time` · `weather` |
| S5 | 진화인력 안전 · 투입 관리 | `resources` · `spread` · `weather` |
| S6 | 유관기관 동원 · 협조 요청 | 요청 표(`requests.s_rows`) · `agencies` |
| E1 | 위험구역 설정 | `villages` |
| E2 | 대피 대상 · 순서 · 시점 | `villages` · `time` |
| E3 | 취약시설 조치 | `vulnerable` · `time` |
| E4 | 대피소 지정 및 관련 통제 | `shelters` · `roads` |
| E5 | 재난문자 · 방송 전파 | `alerts` |
| E6 | 유관기관 요청 | 요청 표(`requests.e_rows`) |

### `sections/` — 문단 자리

항목 입력을 보고 LLM 입력의 `sections`(소제목 + 블록)를 만든다. 판정하지 않는다.

| 블록 | 누가 쓰나 | 칸 |
|---|---|---|
| `action` | **LLM**이 문단 하나를 쓴다 | `id`(S1-a1) · `topic`(주제) · `use`(쓸 `facts` 키) · `anchors`(근거 쪽) · `see`(세부는 다른 항목) |
| `list` · `table` · `note` · `fixed` | **조립기**가 바로 채운다(`writer: 조립기`) | 목록 행 · 표 행 · 안내문 |

- 블록 **주제는 고정**하고, 블록은 **그 사실이 있을 때만** 넣는다(조건에 따라 주제를 바꾸면 처방을 조립기가 정하는 셈)
- `do_not_state` = 비운 칸 이름(LLM이 말하면 안 되는 것), `anchor_basis` = 문단 근거 청크 ID
- `action` 블록이 없는 항목(E6)은 LLM을 부르지 않는다

### `llm_input.py` — 조립

`ITEMS` 순서(S1 ~ E6)대로 항목 입력 → 섹션을 붙이고, `common`(읽는 사람 · 산불 · 제안서 시각 · 현재 단계 · 지휘권자)을 만든다. 여기서도 판정하지 않는다.

## 4. 엔진 · LLM 나누는 기준

| 질문 | 답이 예면 |
|---|---|
| 데이터 · 계산으로 나오나 | 엔진 **사실** |
| 값 하나를 매뉴얼 수치 · 범위 하나에 대 보나 | 엔진 **판정**(J1 ~ J9) |
| 「무엇을 하라」인가(협의 · 요청 · 인계 · 대피명령 · 시한 · 누구에게) | **LLM**(매뉴얼 근거 청크를 보고) |
| 셋 다 아님 | 넘기지 않음(`unknown` · `withheld`) |

엔진 판정은 「값 하나 · 기준 하나」로 단순하게 둔다.

| # | 판정 | 기준 | 어디 |
|---|---|---|---|
| J1 | 구간 | 처음 닿는 P: 0 이미 닿음 · 1 ~ 5 위험구역 · 6 ~ 8 잠재 위험구역 (p.75) | `core/convert.py` |
| J2 | 밤 | 일출 전 · 일몰 뒤. 구간은 시작이나 끝이 밤이면 밤 | `core/sun.py` |
| J3 | 요소별 단계 | 면적 10 · 100 ha, 평균풍속 3 · 11 m/s, 시설 1 · 20동 (p.73) | `core/stage.py` |
| J4 | 대응단계 | J3 셋 중 가장 높은 단계 | `core/stage.py` |
| J5 | 시설피해 우려 | P5 안 주택 + 국가유산 | `core/facility.py` |
| J6 | 지휘권자 | 시 · 도 2곳 · 1,000 ha → 산림청장 / 시 · 군 · 구 2곳 · 100 ha → 시 · 도지사 / 그 밖 시장 · 군수 (p.72 · 법 제34조② · 시행령 제21조①) | `core/commander.py` |
| J7 | 동원 범위 | 단계 → 지상 · 공중 범위 (산림청고시 제2026-94호 별표) | `bundles/mobilization.py` |
| J8 | 대피소 안전 | 대피소 점: P0 ~ P5 제외 · P6 ~ P8 예외 · 밖 안전 | `bundles/shelters.py` |
| J9 | 읍면 Set · Go | 읍면 안 마을의 가장 높은 구간 (p.76) | `bundles/alerts.py` |

값의 근거 등급: **A** 원문 수치 · **B** 원칙 + 해석 · **C** 사례 · **D** 우리가 정한 값 · **E** 근거 없음(넘기지 않음). D는 매뉴얼 · 법령 값으로 바꾸거나, 빼거나, 이유를 적고 남긴다.

## 5. 예시: S1 · E1이 만들어지는 길 (테스트 12:50 예시)

```
[S1]
context  range_areas → P0 48.5 · P5 363 ha / summarize_wind → 현재 3.4 · 평균 2.2 m/s
         count_facilities → 주택 2 / judge_stage ×2 → 현재 1단계 · P5 2단계
         decide_commander ×2 → 지금 의성군수 · 곧 경상북도지사
bundles  stage {now: 1단계, decided_by [area, wind, facility], p5: 2단계, trend: up}
         area {면적, 넘은 기준} / commander {now, next, 이유}
items    facts = now · p5 · change · area · commander, status = escalation_expected
         finding 「현재 기준 1단계(…) · P5 기준 2단계(피해면적 363 ha) → 5시간 안에 2단계 기준 도달 예상 · …」
sections 1. 대응단계 판단 · 격상 검토: a1 현재 기준 단계, a2 5시간 뒤 단계(격상 예상일 때만)
         2. 지휘권 인계: a3 지휘권자

[E1]
context  reached_villages → 괴산리 0 · 신월리 0 · 신안리 1 · 철파리 2 · 정자리 8
bundles  villages {burning: [괴산리, 신월리], p5: [신안리, 철파리], p8: [정자리]}
items    facts.zones(구간별 이름 · 읍면), status = risk_zone
         finding 「위험구역 4곳: 안평면 괴산리(이미 닿음) · … / 잠재 위험구역 1곳: 옥산면 정자리」
sections a1 리 단위 구역 + 조립기 목록(구간별 마을)
```

## 6. 어디를 고치나

| 하려는 것 | 고칠 곳 |
|---|---|
| 사실 · 판정이 틀림(범위 · 단계 · 마을 · 대피소 판정 등) | `core/` · `bundles/` + 그 묶음 테스트 |
| 여러 묶음이 같이 쓰는 계산 | `context.build_context()` |
| 새 입력 자료 | `context.Inputs` + 쓰는 묶음 + `tests/unit/rules_example.py` |
| LLM 문장 · 항목에 넣을 값 | `items/`(`facts` · `finding`) · `sections/`(블록 · `use` · `anchors`) · 에이전트 지시문 |
| 요청 표의 기관 · 요청 문구 | `items/requests.py` |
| 새 항목 | `items/` + `sections/` + `llm_input.ITEMS` |

## 7. 테스트

- `tests/unit/test_rules_*.py`: 묶음 · 항목별 **핵심 경우만**
- `tests/unit/rules_example.py`: 2025-03-22 12:50 의성 예시(`inputs()` 기본, `full_inputs()` 전체 자료)

```bash
uv run pytest tests/unit/test_rules_*.py -q
uv run ruff check rules tests/unit && uv run ruff format --check rules tests/unit
```

## 8. 아직 없는 것

- DB → `Inputs` 어댑터(app 쪽). 기지별 대기 수 = 대기(`AVAILABLE`) 자원 수, 투입 수 = 이 산불에 연결된(`resources.deployed_incident_id`) 자원 수
- 에이전트(지시문 · 근거 검색 · LLM 호출 · 문장 검사)

## 9. 관련 문서 (wfg-docs `docs/설계/`)

- `규칙-엔진-설계.md`: R-xx · L-xx 규칙 번호와 근거(일부는 옛 14개 항목 번호)
- `LLM-입력-정리.md` 「제안 목표와 입력 형식 (11/7)」, `LLM-입력-예시.json` · `LLM-출력-예시.json`
- `DB-설계.md`: 입력 자료가 오는 테이블
