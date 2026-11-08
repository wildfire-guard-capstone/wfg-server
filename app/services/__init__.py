"""잇는 자리: 예측(app.predict) · DB(app.repositories) · 규칙 엔진(rules)을
순서대로 부르고 커밋한다.

- prediction.run_prediction: 현재 화선 → 예측 → 검사 · 저장
- engine.run_engine: 예측 + 바탕 데이터 → 규칙 엔진 → LLM 입력
명령줄: `python -m app.services --help`
"""
