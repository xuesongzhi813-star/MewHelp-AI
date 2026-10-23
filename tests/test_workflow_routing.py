import pytest

from app.workflow.routing import route_intent, resolve_reference, classify_intent
from app.workflow.contracts import BudgetState
from app.workflow.models import ModelGateway
from tests.test_workflow_models import Provider


@pytest.mark.parametrize('intent,want', [('物流','business'),('订单','business'),('商品咨询','knowledge'),('退款退货','refund'),('售后','refund'),('投诉','complaint'),('闲聊','chat')])
def test_all_seven_intents_have_fixed_exit(intent, want):
    assert route_intent(intent) == want


def test_reference_is_byte_for_byte_passthrough():
    assert resolve_reference('  那个\n大一点的呢？') == '  那个\n大一点的呢？'


async def test_invalid_intent_is_explicit_other_after_one_repair():
    provider = Provider()
    provider.parsed = {'intent': 'unknown'}
    decision = await classify_intent('订单1001能退吗', gateway=ModelGateway(provider), budget=BudgetState())
    assert decision.intent == '其他'
    assert decision.confidence == 0
    assert len(provider.calls) == 2


def test_plan_business_does_not_override_policy_exit():
    from app.kb.contracts import QueryPlan
    plan = QueryPlan(raw_query='订单1001能退吗', normalized_query='订单1001能退吗', lexical_query='退货', intent='business')
    assert route_intent('退款退货') == 'refund'
    assert plan.intent == 'business'  # retrieval metadata does not control workflow
