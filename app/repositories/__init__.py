"""DB 읽기 · 쓰기 자리. 예측 · 규칙 엔진은 DB를 모르고, DB를 아는 코드는 여기에만 둔다.

- incidents: 산불 · 화선 · 계정 · 직전 제안서
- predictions: 예측 저장 · 읽기(P1 ~ P8 검사)
- base_data: 바탕 데이터 → 규칙 엔진 자료형
- engine_inputs: 위를 모아 규칙 엔진 입력(rules.context.Inputs)
커밋은 부르는 쪽(services)이 한다.
"""
