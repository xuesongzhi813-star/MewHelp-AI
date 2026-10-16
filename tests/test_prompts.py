import pytest

from app.config import Settings
from app.prompts import SYSTEM_TEMPLATE, build_system_prompt

# ch02 升级说明:SYSTEM_TEMPLATE 由 ch01「查不到实时数据」版升级为工具版
# (接入 function calling 后模型可查订单/物流/商品/FAQ、建工单)。
# 断言口径随之更新:改为断言工具规范的关键词存在、旧措辞不存在;
# ch01 保留的约束(不私自承诺退款赔付、追问限 2 问、超范围转人工、3 段内)继续断言。


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


def test_system_prompt_contains_tool_usage_rules(settings):
    """ch02 工具版:工具使用规范的关键词必须存在。"""
    prompt = build_system_prompt(settings)
    # 优先调用工具查证 + 单轮最多一个工具
    assert "优先调用对应工具查证" in prompt
    assert "一次最多调用一个工具" in prompt
    # 工具失败/无匹配时如实告知 + 转人工,不编造结果
    assert "无匹配" in prompt and "执行失败" in prompt
    assert "绝不编造工具结果" in prompt
    assert "转人工" in prompt


def test_system_prompt_drops_ch01_no_realtime_clause(settings):
    """ch01 的「查不到实时数据」约束措辞必须已被删除(现在查得到了)。

    注意:模板现在正向提到「实时数据」(工具可查的 数据 范围),所以只断言
    旧约束的否定性措辞消失,不断言「实时数据」四个字整体消失。
    """
    prompt = build_system_prompt(settings)
    assert "查不到" not in prompt
    assert "暂时查不到" not in prompt
    # 模板本体同样不再含旧措辞(防止渲染层做替换的假象)
    assert "查不到" not in SYSTEM_TEMPLATE


def test_system_prompt_keeps_ch01_constraints(settings):
    """ch01 保留的约束在工具版中依然存在。"""
    prompt = build_system_prompt(settings)
    assert "不要私自承诺退款、赔付、补偿" in prompt
    assert "一次最多追问 2 个关键问题" in prompt
    assert "超出你能力范围" in prompt
    assert "尽量控制在 3 段以内" in prompt
    # 口语化简体中文要求不变
    assert "简体中文" in prompt
