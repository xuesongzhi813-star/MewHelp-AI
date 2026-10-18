import pytest

from app.config import Settings
from app.prompts import build_system_prompt


@pytest.fixture
def settings() -> Settings:
    return Settings(
        llm_base_url="https://example.com",
        llm_api_key="sk-test",
        llm_model="test-model",
        llm_extra_body={},
        shop_name="测试商城",
        agent_name="小测",
    )


def test_system_prompt_renders_shop_and_agent_name(settings):
    prompt = build_system_prompt(settings)
    assert "测试商城" in prompt and "小测" in prompt
    assert "{" not in prompt    # 无未渲染变量
