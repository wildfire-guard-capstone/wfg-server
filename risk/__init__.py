"""산불 위험도: 예측 범위 P5에 걸친 23개 변수의 등급(1 ~ 5)을 CSV 가중치로 평균한 R(1.00 ~ 5.00).

기준 문서: wfg-docs `docs/설계/위험도-정리.md`, 「산불 발생 후 위험도 정의」(10/5).
설계 결정은 `risk/README.md`.

    from risk.compute import RiskInputs, compute_risk
    result = compute_risk(RiskInputs(p5=..., perimeter=..., grid=..., weather=..., ...))
    result.score      # R
    result.factors    # 변수별 값 · 등급 · 가중치 · 기여

순수 계산만 둔다. FastAPI · DB · `spread` · `rules`를 import하지 않는다.
래스터 파일 읽기 · 쓰기(`grid.save` · `grid.load`)와 물길 거리 계산만
rasterio · scipy를 함수 안에서 import한다.
"""
