import pytest

from app.config import get_settings

REQUIRED_ENV = {
    "LLM_BASE_URL": "https://api.deepseek.com",
    "LLM_API_KEY": "sk-test",
    "LLM_MODEL": "deepseek-chat",
    "LLM_EXTRA_BODY": '{"thinking": {"type": "disabled"}}',
    "LLM_TEMPERATURE": "0.7",
    "HISTORY_TOKEN_BUDGET": "4000",
    "MAX_SESSIONS": "1000",
    "SHOP_NAME": "MewHelp 商城",
    "AGENT_NAME": "小喵",
}


@pytest.fixture
def env_settings(monkeypatch):
    """Provide all settings via env vars so tests never depend on an on-disk .env."""
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_loads_extra_body_as_dict(env_settings):
    s = get_settings()
    assert s.llm_extra_body == {"thinking": {"type": "disabled"}}
    assert s.history_token_budget == 4000


def test_invalid_extra_body_json_raises_clear_error(env_settings, monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", "not json")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="LLM_EXTRA_BODY"):
        get_settings()


@pytest.mark.parametrize('key,value', [
    ('QUERY_TIMEOUT', '0'), ('RERANK_TIMEOUT', '-1'),
    ('EMBEDDING_TIMEOUT', '0'), ('KNOWLEDGE_TOOL_TIMEOUT', '0'),
    ('ANSWER_TIMEOUT', '0'), ('RERANK_MIN_SCORE', '1.1'),
    ('RERANK_MIN_SCORE', 'nan'), ('KB_CORPUS_VERSION', 'guess'),
    ('KNOWLEDGE_STRATEGY', 'python_bm25'),
])
def test_ch04_settings_reject_invalid_operational_values(env_settings, monkeypatch, key, value):
    from app.config import Settings
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings(_env_file=None)
