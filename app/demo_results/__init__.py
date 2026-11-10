"""김지오 전용 · 임시 결과 조회 API(/api/auth · /api/incidents, 결과 파일 · JSON 계정).

GPU 서버에서 만든 의성 데모 결과 묶음(`python -m demo.uiseong.export_bundle`)을
웹 화면(wfg-client)이 읽게 내주는 읽기 전용 API다. DB를 쓰지 않는다 —
결과는 RESULTS_DIR 폴더, 계정은 ACCOUNTS_FILE(JSON), 토큰 서명 키는 AUTH_SECRET.
주 API는 DB를 쓰는 /api/v1(app/routers)이고, 이 폴더는 그것이 화면에 붙을 때까지만 쓴다.

계정 관리: `python -m app.demo_results.accounts add --file accounts.json --login cmd01 …`

지우는 법(이 기능을 통째로 없앨 때):
1. 이 폴더 `app/demo_results/`와 테스트 폴더 `tests/unit/demo_results/`를 지운다
2. `app/main.py`에서 「김지오 전용 · 임시」 표시가 붙은 두 줄(import · include_router)을 지운다
   (`app.demo_results.api.router`)
3. `.env.example`의 RESULTS_DIR · ACCOUNTS_FILE · AUTH_SECRET · TOKEN_TTL_MINUTES 줄과 README의
   「결과 조회 API(임시)」 절을 지운다(선택: `demo/uiseong/export_bundle.py`도 이 API용 묶음 도구)
"""
