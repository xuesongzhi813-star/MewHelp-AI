import pytest
from langchain_core.messages import HumanMessage

from app.config import Settings
from app.context.budget import (
    ContextBudgetError, cumulative_turn_limit, derive_budget, ensure_fits,
    validate_startup,
)


def demo(**changes):
    return Settings(_env_file=None, **dict(model_context_window=18000,
        max_output_tokens=2000, max_user_input_tokens=2000,
        max_agent_steps=3, tool_result_max_tokens=1200, rerank_top_k=5, **changes))


def test_demo_budget_is_5650_3954_1695():
    b = derive_budget(demo(), system_tokens=1500, summary_tokens=0)
    assert (b.system,b.evidence,b.summary,b.output,b.safety,b.peak) == (2000,1500,1000,2000,850,5000)
    assert (b.history,b.layer1,b.layer2) == (5650,3954,1695)
    assert b.history-b.layer1-b.layer2 == 1


def test_system_and_summary_actual_can_shrink_history():
    b = derive_budget(demo(), system_tokens=2400, summary_tokens=1600)
    assert b.history == 4650
    assert (b.system,b.summary) == (2400,1600)


@pytest.mark.parametrize('window',[100,12350,12351])
def test_budget_is_never_negative(window):
    b = derive_budget(demo().model_copy(update={'model_context_window':window}), system_tokens=0, summary_tokens=0)
    assert min(b.history,b.layer1,b.layer2) >= 0


def test_no_single_exchange_startup_rejected():
    with pytest.raises(ContextBudgetError,match='上下文预算不足'):
        validate_startup(demo().model_copy(update={'model_context_window':5000}),schemas=[])
    assert validate_startup(demo(),schemas=[]).history > 0


def test_actual_payload_cannot_exceed_window():
    with pytest.raises(ContextBudgetError,match='上下文预算不足'):
        ensure_fits([HumanMessage(content='长'*17000)],schemas=[],output_cap=2000,settings=demo())


def test_cumulative_cap_is_separate_and_explicit_cap_is_strict():
    assert cumulative_turn_limit(demo()) == 9*18000
    assert cumulative_turn_limit(demo(context_turn_token_budget=12000)) == 12000


@pytest.mark.parametrize('changes',[
    {'max_agent_steps':1},{'context_cjk_tokens_per_char':0},
    {'context_safety_ratio':float('nan')},{'rerank_top_k':11},
])
def test_invalid_context_configuration_rejected(changes):
    with pytest.raises(ValueError):
        Settings(_env_file=None,**changes)
