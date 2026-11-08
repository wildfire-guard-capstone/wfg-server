"""잇는 자리: 예측(app.predict) · DB(app.repositories) · 규칙 엔진(rules)을
순서대로 부르고 커밋한다.

- prediction.run_prediction: 현재 화선 → 예측 → 검사 · 저장
- engine.run_engine: 예측 + 바탕 데이터 → 규칙 엔진 → LLM 입력
- results: 결과 조회 API가 읽는 결과 묶음(demo.uiseong.export_bundle) — 나중에 DB 구현으로 바꿈
- accounts: 로그인 계정 파일 관리(`python -m app.services.accounts`)
명령줄: `python -m app.services --help`
"""
