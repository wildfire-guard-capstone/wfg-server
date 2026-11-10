"""데모 2단계가 규칙 엔진에 넘길 바람 파일 고르기(덱마다 이름이 다름)와 뷰어 머리말의 LLM 이름."""

from pathlib import Path

from demo.uiseong import build_viewer, step2_rules

NAMELIST_FINAL = """&INPUTS
WS_FILENAME             = 'ws_gust'  ! mph, 10 m, 시간 안 가장 센 1분 평균풍속
WD_FILENAME             = 'wd_obs'   ! 불어오는 방향(도)
/
"""
NAMELIST_OLD = """&INPUTS
WS_FILENAME             = 'ws'       ! mph, 10 m 높이 시간평균
WD_FILENAME             = 'wd'
/
"""


def _deck(tmp_path: Path, namelist: str, files: tuple[str, ...]) -> Path:
    (tmp_path / "inputs").mkdir()
    (tmp_path / "elmfire.data").write_text(namelist, encoding="utf-8")
    for f in files:
        (tmp_path / "inputs" / f"{f}.tif").write_bytes(b"")
    return tmp_path


def test_wind_prefers_mean_files_when_present(tmp_path):
    # 11/9 최종 덱 + 시간평균 ws · wd를 함께 둔 덱 → 평균풍속
    deck = _deck(tmp_path, NAMELIST_FINAL, ("ws_gust", "wd_obs", "ws", "wd"))
    ws, wd, note = step2_rules.wind_files(deck)
    assert (ws, wd) == ("ws", "wd")
    assert "시간평균" in note


def test_wind_falls_back_to_namelist_and_flags_gust(tmp_path):
    # 11/9 최종 덱 그대로 → namelist의 ws_gust · wd_obs, 돌풍이라는 표시
    deck = _deck(tmp_path, NAMELIST_FINAL, ("ws_gust", "wd_obs"))
    ws, wd, note = step2_rules.wind_files(deck)
    assert (ws, wd) == ("ws_gust", "wd_obs")
    assert "돌풍" in note


def test_wind_old_deck_unchanged(tmp_path):
    deck = _deck(tmp_path, NAMELIST_OLD, ("ws", "wd"))
    assert step2_rules.wind_files(deck)[:2] == ("ws", "wd")


def _round(model, state="ok"):
    return {"llm_model": model, "proposal": [{"state": state}]}


def test_model_label():
    assert build_viewer.model_label([_round("exaone-4.5-33b-awq")] * 4) == "EXAONE 4.5 33B"
    assert build_viewer.model_label([_round("exaone-4.0-32b-awq")]) == "EXAONE 4.0 32B"
    assert build_viewer.model_label([_round("gemma-x")]) == "gemma-x"
    # 11/9 이전 실행(llm_model 없음)은 모델을 지어내지 않는다
    assert build_viewer.model_label([_round(None)]) == "EXAONE(모델 기록 없음)"
    assert build_viewer.model_label([_round(None, "not_run")]) == "LLM 생성 없음"


def test_template_has_no_fixed_model_name():
    tpl = (Path(build_viewer.__file__).parent / "viewer_template.html").read_text(encoding="utf-8")
    assert tpl.count("__MODEL__") == 1
    assert "EXAONE 4.0" not in tpl and "EXAONE 4.5" not in tpl
