"""확산속도 배율(ELMFIRE_ADJ): 비우면 기본 1.25, 숫자가 아니거나 0 이하면 거절."""

import pytest

from app.predict.adj import DEFAULT_ADJ, parse_adj


@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_is_default(value):
    assert parse_adj(value) == DEFAULT_ADJ == 1.25


@pytest.mark.parametrize("value, expected", [("1", 1.0), ("1.5", 1.5), (2.0, 2.0)])
def test_number(value, expected):
    assert parse_adj(value) == expected


@pytest.mark.parametrize("value", ["abc", "0", "-1", "inf", "nan"])
def test_bad_value(value):
    with pytest.raises(ValueError, match="ELMFIRE_ADJ"):
        parse_adj(value)


def test_settings_pass_multiplier_to_in_process_elmfire(monkeypatch, tmp_path):
    from app.core.config import Settings
    from app.services import prediction

    def predictor(**env):
        s = Settings(_env_file=None, predictor_deck=str(tmp_path), **env)
        monkeypatch.setattr(prediction, "get_settings", lambda: s)
        return prediction.elmfire_predictor_from_settings()

    monkeypatch.delenv("ELMFIRE_ADJ", raising=False)
    monkeypatch.delenv("ELMFIRE_WORKER_URL", raising=False)
    assert predictor().adj == DEFAULT_ADJ
    assert predictor(elmfire_adj=1.0).adj == 1.0
    with pytest.raises(ValueError, match="ELMFIRE_ADJ"):
        predictor(elmfire_adj=-1.0)
