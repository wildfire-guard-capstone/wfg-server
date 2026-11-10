# AI 어시스턴트 구조서 — 질의응답(F-29) · 상황 정정(F-30)

작성 2026-11-09. 기준 문서: wfg-docs `docs/설계/API-명세서.md`(AI-01 · AI-02 · COR-01 · COR-02 · PROP-05),
`DB-설계.md` 2.5(제안 다루기), `LLM-입력-정리.md` A-01~A-05.

## 1. 한눈에

```
POST /api/v1/incidents/{id}/assistant/messages  {input_text, proposal_id?, item_code?}
  app/services/assistant.ask
    build_context: 산불 · 지금 상황(incidents.situation) · 제안서(context 16묶음 + 항목) · 직전 제안서 · 최근 정정 · 관할 마을
    assistant.Assistant.handle
      intent      질문 / 정정 / 그 밖 — 규칙(정규식) 먼저, 애매하면 EXAONE
      ├ 질문 → answer.plan_rules + plan_llm(도구 고르기, 둘을 합침)
      │        → tools.ToolBox(도구 16개: 상황 값 Fact · 근거 원문 chunk)
      │        → EXAONE 답(문장마다 data = 상황 값 D번호, docs = 근거 문서 번호)
      │        → check(출처 · 숫자 · 시각 · 마을 이름) → 1번 다시 → 걸린 문장은 뺌 → 조립([k] 다시 매김)
      │        → qa_messages 1행(answer · evidence · detail) · 근거를 못 찾으면 이벤트 QA_NO_EVIDENCE
      └ 정정 → correction.parse_llm(없거나 깨지면 parse_rules) → 대상 찾기 · 허용 항목 · 범위 검사
               → 확인안(CORRECTION_PREVIEW) + 확인 토큰. 아무것도 저장하지 않음
POST /corrections {confirmation_token}            COR-01
  토큰 · 상황 버전 · 변경 전 값 대조 → situation_corrections 1행 + incidents.situation + SITUATION_CORRECTED (한 트랜잭션)
  → 예측이 있으면 작업 PROPOSAL_REGENERATION: 같은 예측 + 바뀐 상황 → 규칙 엔진 → 제안 작성기 → 제안서 vN.(k+1), trigger CORRECTION
GET  /assistant/messages · /corrections          AI-02 · COR-02 (id 내림차순, 커서)
POST /proposal-regenerations                      PROP-05 (정정 뒤 제안 생성 실패 재시도)
```

`assistant/`는 DB · FastAPI를 모르고 표준 라이브러리만 쓴다(`agent/`와 같음). 앱은 DB에서 `Context`를 만들고,
데모 · 평가는 의성 데모 회차 폴더에서 만든다(`assistant.context.from_round`). 그래서 GPU 서버에서 그대로 돌려 볼 수 있다.

## 2. 파일

| 파일 | 하는 일 |
|---|---|
| `assistant/context.py` | `Context`(산불 · 상황 · 제안서 · 직전 · 정정 · 마을 목록 · 투입 상한 · 상황 버전), 데모 회차 → Context |
| `assistant/intent.py` | 입력 구분. 「몇 · 얼마」가 있으면 질문, 질문 신호만 → 질문, 정정 신호만 → 정정, 둘 다 · 둘 다 없음 → EXAONE |
| `assistant/tools.py` | 도구 16개(아래 3장), `Fact`(자료 경로 · 이름 · 값), `change_label`(정정 1건 → 사람이 읽는 말) |
| `assistant/prompt.py` | 프롬프트 4개(구분 · 도구 고르기 · 답 · 정정 해석)와 json_schema |
| `assistant/answer.py` | 도구 고르기(규칙 + LLM 합침) → 재료 모으기 → 답 쓰기(LLM · 규칙) → 조립 |
| `assistant/check.py` | 답 문장 검사(4장) |
| `assistant/correction.py` | 정정 해석(LLM · 규칙) → 검사 → 변경안(5장) |
| `assistant/service.py` | 입구 `Assistant.handle`, LLM · 근거 서버 연결 실패 → `AssistantUnavailable`(503) |
| `assistant/cli.py` | 데모 회차로 묻기 · 일괄 평가(`assistant/eval/questions.json`, 질문 20 + 정정 11) |
| `app/services/assistant.py` | DB → Context, 답 저장, 확인 토큰, 정정 반영(`apply_changes`), 제안서 다시 만들기 작업 |
| `app/routers/assistant.py` · `app/schemas/assistant.py` · `app/repositories/assistant.py` | API · 요청 본문 · qa_messages / situation_corrections / 관할 마을 |
| `migrations/versions/7c1e2a9d4b10_qa_messages_evidence.py` | `qa_messages.evidence`(jsonb [] — 인용 원문, 제안 항목 evidence 꼴 + k) · `detail`(jsonb {}) |

## 3. 도구

LLM은 도구 이름과 인자만 고른다. 값은 도구가 DB에 저장된 엔진 결과에서 읽는다(계산 · 판정하지 않음).
없는 값은 엔진 묶음의 `unknown` · 항목 `withheld`를 「빠진 정보」 Fact로 넘겨, LLM이 「~ 정보가 없어」라고 근거 있게 답하게 한다.

| 도구 | 읽는 곳 | 인자 |
|---|---|---|
| `overview` | 산불 · 제안서 버전 · 현재/5시간 뒤 단계 · 현재 지휘권자 · 면적 | — |
| `stage` | `context.stage` · `commander` + 판정 근거 원문(`basis`) | — |
| `spread` | `context.area`(P1~P8 · 100 ha 넘는 시점) · `spread`(방향 · 다음 마을) | — |
| `weather` | `context.weather` · 일몰/일출 | — |
| `resources` | **지금 상황** `situation.deployed`(정정 반영) · 동원 범위 · 기지 | — |
| `villages` · `village` | `context.villages` + 지금 대피 상태(`situation.villages`) · 대피소 후보 · 문자 기록. `villages`는 대피명령 · 완료 보고가 아직 없는 마을을 불이 닿는 순서로 한 줄 더 준다(「다음은 어디?」용) | 마을 이름 |
| `shelters` | `context.shelters` | 마을 이름(없으면 전체) |
| `facilities` · `roads` · `alerts` · `agencies` | 취약시설 · 송전선 · 국가유산 · 주택 / 도로 구간 / Go·Set · 문자 / 소방 · 경찰 · 응급의료 | — |
| `item` | 항목의 판정 · 제안 문단 · 목록 · 표 · ※ · 빠진 정보 + **그 항목이 인용한 원문** | 항목 코드 |
| `proposal` · `changes` | 항목 요약 · 발동 안 한 항목 / 직전 버전 대비 바뀐 항목 · 최근 정정. 「바뀐 항목」은 제안서 비교(PROP-04)와 같은 `app/services/proposal_compare.changes`로 판단한다(표현만 바뀐 것은 변경 아님) | — |
| `search` | 근거 서버 `/search`(표준매뉴얼 · 법령, k = 4) | 검색할 말 |

Fact의 `ref`는 답변 문장의 `data_refs`로 화면에 간다(예: `situation.deployed.helicopter.total`,
`context.area.slices[p=5].ha`, `items.E2.paragraphs[0]`).

## 4. 답 쓰기와 검사

- **도구 고르기**: 규칙(핵심어 · 마을 이름 · 항목 코드 · 「왜 · 근거 · 누가」 → `search`)과 EXAONE(`plan`)을 합친다. 규칙이 잡은 도구는
  LLM이 빠뜨려도 들어가고, 「이 항목 질문」이면 그 항목이 맨 앞이다. 최대 5개. LLM이 OUT_OF_SCOPE라 해도 규칙이 핵심어를 잡았으면 산불 질문으로 본다.
- **재료 번호**: 상황 값 D1…(최대 70), 근거 문서 [1]…(판정 · 인용 근거 먼저, 검색은 뒤, 최대 8, 본문 700자).
- **json_schema**: `sentences[{text ≤ 220자, data: D번호 enum, docs: 번호 enum}] 1~5`(재료가 없을 때만 0개 허용) · `insufficient[≤ 3]`. 없는 번호는 낼 수 없다.
- **검사**(`check.py`): 출처 없음 · 단 출처에 없는 숫자/시각(단 출처에 없고 다른 상황 값 · 근거 문서에 있으면 그것을 출처로 더함) ·
  자료 어디에도 없는 「○○리」 · 220자 초과 · 「~다/요/오」로 안 끝남 · 문장 안 D번호 · 「상황 값」 · 「(빠진 정보)」 · 「MVP」 같은 내부 말.
  판단 금지(빠진 정보를 다른 값으로 대신 판단): 헬기 운항 · 운용 가능, 순간풍속, 추가 N대 투입, 「사전대피 = 5시간 기준」.
  걸리면 오류를 보여 주고 1번 다시 쓰게 한 뒤, 그래도 걸린 문장은 뺀다. 「2단계 · P5 · S3 · 제21조 · p.75」의 숫자는 이름이라 보지 않는다.
- **다듬기**: 문장 안 [1] 지움, 「[p.75]」 괄호 벗김, 「~니다입니다」 → 「~니다」, 「(P5)」 지움, 「~이다 · ~한다」 → 「~입니다 · ~합니다」.
- **묻지 않은 「없음」 빼기**: 「○○는 정보가 없어 판단할 수 없습니다」의 주어가 질문(「몇 대 더」 → 추가 · 대수처럼 말투를 자료 낱말로 넓혀 봄) ·
  지정 항목의 빠진 정보와 겹치지 않으면 그 문장을 조용히 뺀다(다시 묻지 않음, trace `unasked`).
- **조립**: 실제로 단 문서만 처음 나온 순서로 [1], [2] …(표기가 같은 쪽은 한 번호로 묶음), 문장 끝 마침표 앞에 「 [1]」. 질문이 물었는데 못 찾은 것은 끝에
  「○○는 자료와 근거 문서에서 찾지 못해 답하지 않았습니다.」(근거 부족 규칙 11/6 — 고정문 대신 무엇이 없는지 적음).
- **대신 답하기**: EXAONE 답이 비거나 두 번 다 검사에서 떨어지면 상황 값 문장(질문과 겹치는 것, 그중 「○○ 정보는 없습니다」 먼저)으로 답한다
  (trace `fallback: rules`). 상황 값도 근거 문서도 없을 때만 「…찾지 못해 답하지 않았습니다」 + 이벤트 QA_NO_EVIDENCE.
- **규칙 모드**(`ASSISTANT=rules`, LLM 없음): 도구 순서대로 Fact 문장을 잇는다. 프론트 연결 · CI용이며 품질은 낮다.

## 5. 상황 정정

정정할 수 있는 칸만 바꾼다(DB 설계 2.5). AI는 자유 문장을 이 칸들의 변경으로 바꾸기만 한다.

| 말 | 변경(CorrectionChange) | 비고 |
|---|---|---|
| 「괴산리 대피 완료」 「다 빠져나갔어」 | village · evacuated false → true | 「대피명령 내렸어」 evac_order, 「사전대피 끝」 pre_evac_done(엔진이 읽는 칸) |
| 「산림청 헬기 2대 추가」 | deployed · helicopter.forest 5 → 7, helicopter.total 23 → 25 | 추가 · 더 · 도착 = 더함, 철수 · 복귀 = 뺌, 총 · 지금 · 현재 = 그 수로 |
| 「인력 50명 추가」(기관 없음) | crew.other(기타) + total, 확인 카드에 메모 | 차량도 같음 |
| 「헬기 2대 추가」(기관 없음) | helicopter.unknown(미상) 0 → 2, total 23 → 25, 확인 카드에 메모 | 6장 1번 |
| 「미상 헬기 2대는 산림청이었어」 | helicopter.unknown 2 → 0, helicopter.forest 5 → 7(합계 줄 없음) | 기타 인력 · 차량도 같음. 뺄 미상이 모자라면 어느 기관인지 물음 |
| 「재난문자 보냈어」 | situation · disaster_sms_sent | |
| 「2단계 발령됐어」 「바람이 세졌어」 「부상자」 「화선」 | 반영 안 함, 이유 안내(UNSUPPORTED) | 대응단계는 시스템 판정, 기상은 다시 예측 … |

- **대상 찾기**: 마을 이름(「괴산」 · 「괴산1리」도) → ① 이번 제안서의 마을 ② 산불 시군구 ③ 인접 시군구 순으로 좁힌다. 없으면 422
  TARGET_NOT_FOUND, 같은 단계에 여러 읍면이면 422 AMBIGUOUS_TARGET(「안평면 하화리」처럼 다시).
- **LLM 해석 검사**: LLM이 낸 투입 수는 입력에 그 수(숫자 · 한글 수사)가 있을 때만 받는다. 마을은 json_schema enum(관할 마을 이름)으로 묶는다.
- **합계만 있는 보고**: `total`이 기관 칸 합보다 크면 남는 몫은 미상(헬기) · 기타(인력 · 차량) 칸 몫으로 본다. 엔진 입력(`deployed_of`) · 확인안 ·
  COR-01 대조(`slot_value`)가 같은 규칙이다(6장 1번).
- **범위**: 0 아래로 내려가면 422 INVALID_COUNT. 상한 = 자원 테이블의 대기 + 이 산불에 투입된 대수(`repo.capacity`, 칸별: 산림청 ·
  지자체 임차 · 소방 헬기, 진화차 · 소방차). 확인안에서는 넘는 줄만 반영 안 함(ignored)으로 보이고, COR-01에서 자원 현황을 다시 읽어
  넘으면 409 RESOURCE_CAPACITY_EXCEEDED. 군 · 경찰 · 미상 · 기타 · 인력과 그 칸 자원이 하나도 등록되지 않은 경우는 상한이 없다(6장 2번).
- **확인 토큰**: HS256, 서명 키 = JWT 비밀에서 「correction-preview」 용도로 갈라 낸 키(로그인 토큰으로 못 씀). 담는 것: 사용자 · 산불 ·
  상황 버전 · 원문 · 변경안 · 만료(10분). 화면은 변경안을 다시 보내지 않는다.
- **확인(COR-01)**: 산불 행 잠금(FOR UPDATE) → 종료 · 상황 버전 · 변경 전 값 대조(다르면 409 STALE_SITUATION — 같은 토큰을 두 번 보내도 여기서 걸림)
  → 반영(마을 · 투입 칸의 `reported_at` = 확인 시각) → 커밋 → 제안서 다시 만들기 작업. 작업을 못 열면(다른 계산 중) proposal_operation = null,
  proposal_stale = true → PROP-05. 작업은 저장 직전에 종료 · 새 예측 · 더 새로운 정정이 있으면 저장하지 않는다(옛 상황 제안서를 최신으로 보이지 않게).

## 6. 정한 것(11-10)

1. **합계만 바꾸는 정정 → 헬기 「미상」 칸**: 엔진 입력(`engine_inputs.deployed_of`)은 `total`을 버리고 기관 칸을 다시 더하므로, 헬기 기관 칸에
   `unknown`(미상)을 더했다(엔진 라벨 `rules/bundles/resources.LABELS` 포함). 기관을 모르는 헬기는 미상, 인력 · 차량은 기타 칸에 넣고, 기관을
   알게 되면 「미상 헬기 2대는 산림청」으로 옮긴다. 기본 흐름은 자원 관리자가 올린 가용 자원을 통합지휘권자가 보고 배치하는 것이다.
2. **투입 상한 = 자원 테이블 기준**: 칸별로 대기 + 이 산불 투입 대수(5장 「범위」).
3. **위기경보(alert_level)**: 정정 항목이 아니라 격상 · 조정 메뉴를 따로 두는 쪽으로(별도 작업 — DB 칸 · API · 명세). 지금은 정정 항목에서 뺀다.
4. **응답에 더한 칸은 명세에 올린다**: QaMessage `route` · `sentences` · `insufficient` · `no_evidence` · citations의 `label` · `origin` · `text`,
   CorrectionPreview `ignored` · `notes` · `summary` · `proposal_regenerates`.
5. **이벤트 이름**: QA_NO_EVIDENCE(ITEM_NO_EVIDENCE와 같은 꼴), SITUATION_CORRECTED(DB 설계에 있는 이름), PROPOSAL_FAILED(PREDICTION_FAILED와
   같은 꼴 · 같은 details: operation_id + code · message, 여기에 correction_id). 서버 코드 · DB 설계의 이벤트 이름과 대조해 맞음.
6. **API 서버 → GPU 서버**: `ASSISTANT=exaone`이면 `LLM_URL` · `EVIDENCE_URL`이 API 서버에서 닿아야 한다(EC2라면 역터널). 닿지 않으면 바로
   503 AI_UNAVAILABLE. GPU 서버 LLM은 필요할 때만 켜는 것이 기본(상시도 가능). 기본 설정은 `ASSISTANT=rules`라 LLM 없이도 동작한다.

## 7. 시험

- 단위(CI): `tests/unit/test_assistant_correction.py` · `test_assistant_answer.py`(도구 16개 × 예측 전/후, 가짜 LLM으로 계획 · 답 · 다시 쓰기 ·
  근거 없음 · 범위 밖 · 연결 실패) · `test_assistant_app.py`(토큰 · 반영 · 엔진 어댑터가 반영 값을 읽는지).
- 통합(DB): `tests/integration/test_assistant.py` — 질문 저장 · 목록, 예측 전 질문, 권한, 정정 확인 → 제안서 v·2(CORRECTION, 엔진 context에
  정정 값), 같은 토큰 재확인 409, 거절 사례, 예측 전 정정, 종료 산불, PROP-05. `WFG_INTEGRATION=1 uv run pytest tests/integration`.
- EXAONE 평가: `assistant/eval/questions.json`을 데모 회차 1(12:50 의성군수) · 4(14:20 경상북도지사)에 `assistant.cli`로 돌린다.
  질문 20 + 정정 11 = 31개 × 2회차. A6000 한 장(EXAONE 4.5 33B AWQ, vLLM) + 근거 서버(CPU).

### 7.1 EXAONE 평가 이력(2026-11-09 ~ 11-10)

| 차수 | 결과(62개 입력) | 찾은 결함 → 고친 것 |
|---|---|---|
| 1차 | 빈 답 0 · 「…찾지 못해 답하지 않았습니다」 꼬리 41 · 정정 3개 틀림 | 프롬프트 예시 「추가 투입 헬기 대수」를 아무 답에나 꼬리로 붙임 → 예시 삭제 · 질문과 겹치는 것만 남김. 「[상황 값] D18」 노출 → 내부 말 검사. 「밤에도 헬기를 띄울 수 있습니다」 → 판단 금지. 정정 LLM이 박곡리 → 도옥리, 군 헬기 → 지자체, 인력 빠뜨림 → 입력에 있는 이름 · 기관만 받고 규칙 해석과 합침. 같은 쪽 근거 [1][2][3] → 한 번호 |
| 2차 | 시작 못 함 | 공용 GPU 빈 메모리가 확인 직후 줄어 vLLM 거부 → 여유 5 GB · 다시 시도. util 0.6 안팎은 엔진 시작 실패 → GPU 한 장에 38.7 GB 빌 때만 |
| 3차 | 빈 답 8 · 정정 모두 기대대로 | 묻는 값(대피 시한 · 추가 대수)이 없으면 아는 값까지 비우고 insufficient만 냄 → 재료가 있으면 문장 1개 이상(스키마) · 프롬프트 6번 · 그래도 비면 상황 값 문장으로 대신 답. 「노출됩니다입니다」 · 「(P5)」 · 「MVP에서 제외」 · 「생성 상태」 · 「범위 안 0 km」 · Set 읍면을 문자 대상 아님이라고 함 → 다듬기 · 값 문구 고침 |
| 4차 | 빈 답 0 · 묻지 않은 「헬기 운항 가능 여부 … 판단할 수 없습니다」 약 10번 | 3차 프롬프트의 예시 목록을 베낌 → 묻지 않은 「없음」 문장 빼기. 회차 4 「다음은 어디를 대피?」에 이미 닿은 석탑리 · 신안리를 건너뜀 → `villages`가 대피명령 · 완료 보고 없는 마을을 도달 순서로 줌. 「사전대피 = 5시간 기준」 → 판단 금지 |
| 5차 | 빈 답 0 · 묻지 않은 꼬리 0 | 「몇 대 더 불러야?」의 답 「추가 투입 대수 … 판단할 수 없습니다」까지 뺌 → 질문 말투를 자료 낱말로 넓혀 봄. 「풍속이 줄어 헬기 운용이 가능해지므로」 → 「운용」도 금지 |
| 6차 | 빈 답 0 · 대체 답 0 · 꼬리 1 · 정정 모두 기대대로 | 「정보가 빠져 있습니다」 뒤에 같은 꼬리, 주어 없는 「자료에 없는 정보는 판단할 수 없습니다」 → 「빠져 있」을 없다는 말로 봄 · 군말 빼기(이 둘은 단위 시험으로만 확인) |

- 시간(공용 GPU가 한가할 때): 답 7~52초(LLM 2~3번), 정정 2~8초, 2회차 62개에 약 16분. GPU 경합 때는 두 배.
- 남은 한계(검사로 못 잡는 해석 오류): 회차 4 도로 답이 신월리 구간을 「의성읍(철파리)에 있는」이라고 붙임, 취약시설 0곳인데 「위험한지 판단할 수
  없습니다」를 덧붙임, 대피소 후보 중 하나를 골라 권하는 일이 가끔 있음.
- 평가 도구(`assistant.cli`)에는 시군구 마을 목록을 넣지 않아 회차 4 「박곡리 사전대피 끝났어」가 TARGET_NOT_FOUND다(박곡리가 그 회차 제안서 마을이 아님).
  API는 DB의 시군구 · 인접 시군구 마을로 찾는다.
