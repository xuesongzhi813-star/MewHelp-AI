import pytest

from app.config import get_settings


def test_settings_loads_extra_body_as_dict():
    get_settings.cache_clear()
    s = get_settings()
    assert s.llm_extra_body == {"thinking": {"type": "disabled"}}
    assert s.history_token_budget == 4000


def test_invalid_extra_body_json_raises_clear_error(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", "not json")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="LLM_EXTRA_BODY"):
        get_settings()