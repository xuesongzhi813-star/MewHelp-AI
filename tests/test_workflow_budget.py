"""Budget regressions: schema/input cost, reservations, actual usage and deadlines."""
import time

import pytest
from langchain_core.messages import HumanMessage

from app.config import Settings
from app.workflow.budget import BudgetExceeded, reserve_call, settle_call
from app.workflow.contracts import BudgetState


def test_defaults_are_6_12000_90():
    settings = Settings(_env_file=None)
    assert settings.agent_max_tool_calls == 6
    assert settings.agent_token_budget == 12000
    assert settings.agent_turn_timeout == 90
    assert settings.chat_workflow_enabled is False
    with pytest.raises(ValueError):
        Settings(_env_file=None, agent_max_tool_calls=0)


def test_reservation_counts_schema_and_bounds_output():
    budget = BudgetState(token_limit=100)
    reservation = reserve_call(budget, [HumanMessage(content="你好")], schema_text="查" * 20, output_cap=100)
    assert reservation.input_tokens >= 22
    assert reservation.output_cap == 100 - reservation.input_tokens
    assert budget.charged_tokens == 100  # concurrent calls cannot reuse reserved tokens
    with pytest.raises(BudgetExceeded):
        reserve_call(budget, [], schema_text="", output_cap=10)


def test_usage_missing_is_estimated():
    budget = BudgetState(token_limit=1000)
    reservation = reserve_call(budget, [HumanMessage(content="你好")], schema_text="", output_cap=20)
    settle_call(budget, reservation, usage=None, output_text="您好")
    assert budget.charged_tokens == reservation.input_tokens + 2
    assert budget.estimated_calls == 1
    with pytest.raises(ValueError):
        settle_call(budget, reservation, usage=None, output_text="您好")


def test_usage_actual_replaces_reservation():
    budget = BudgetState(token_limit=1000)
    reservation = reserve_call(budget, [], schema_text="", output_cap=50)
    settle_call(budget, reservation, usage={"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}, output_text="ok")
    assert budget.charged_tokens == 19
    assert budget.actual_calls == 1


def test_expired_deadline_prevents_reservation():
    with pytest.raises(BudgetExceeded):
        reserve_call(BudgetState(deadline=time.time() - 1), [], schema_text="", output_cap=10)


def test_tool_arguments_count_toward_estimate():
    from langchain_core.messages import AIMessage
    message = AIMessage(content="", tool_calls=[{"name": "query_order", "args": {"order_id": "长" * 80}, "id": "call1"}])
    with pytest.raises(BudgetExceeded):
        reserve_call(BudgetState(token_limit=60), [message], schema_text="", output_cap=10)
