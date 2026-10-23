import pytest
from langchain_core.messages import HumanMessage
from app.workflow.contracts import BudgetState, IntentDecision
from app.workflow.understanding import UnderstandingResult, understand_question


def test_valid_confidence_is_retained():
    assert IntentDecision(intent='订单', confidence=.9).confidence == .9


@pytest.mark.parametrize('confidence', [float('nan'), float('inf'), -.1, 1.1, True, '0.9'])
def test_invalid_confidence_is_rejected(confidence):
    with pytest.raises(ValueError):
        IntentDecision(intent='订单', confidence=confidence)


class Gateway:
    def __init__(self, result):
        self.result = result

    async def understand(self, messages, budget):
        return UnderstandingResult(**self.result)


def gateway(question, status='unchanged', order=None, source=None):
    return Gateway(dict(standalone_question=question, reference_status=status,
                        order_id=order, source_message_id=source))


async def test_complete_question_is_byte_for_byte_passthrough():
    q = '  订单1001能退吗？\n'
    result = await understand_question(q, history=[], gateway=gateway('订单1001可以退货吗？'), budget=BudgetState())
    assert result.standalone_question == q


async def test_order_invention_is_not_accepted():
    result = await understand_question('它能退吗？', history=[HumanMessage(content='订单1001', id='a')],
        gateway=gateway('订单9999能退吗？', 'resolved', '9999', 'a'), budget=BudgetState())
    assert result.order_id is None
    assert result.standalone_question == '它能退吗？'


async def test_resolved_order_must_exist_in_named_source():
    history = [HumanMessage(content='你好', id='a'), HumanMessage(content='订单1001', id='b')]
    result = await understand_question('它能退吗？', history=history,
        gateway=gateway('订单1001能退吗？', 'resolved', '1001', 'a'), budget=BudgetState())
    assert result.order_id is None


async def test_ambiguous_reference_never_selects_an_order():
    result = await understand_question('它能退吗？', history=[HumanMessage(content='订单1001和订单1002', id='both')],
        gateway=gateway('订单1002能退吗？', 'ambiguous', '1002', 'both'), budget=BudgetState())
    assert result.standalone_question == '它能退吗？'
    assert result.order_id is None


async def test_history_order_can_complete_reference():
    result = await understand_question('它能退吗？', history=[HumanMessage(content='订单1001的物流', id='a')],
        gateway=gateway('订单1001能退吗？', 'resolved', '1001', 'a'), budget=BudgetState())
    assert result.standalone_question == '订单1001能退吗？'
    assert result.order_id == '1001'


async def test_rewrite_cannot_drop_negation_amount_or_deadline():
    q = '订单1001签收8天，199元，没有洗过，还能退吗？'
    result = await understand_question(q, history=[],
        gateway=gateway('订单1001签收7天，能退吗？', 'resolved', '1001'), budget=BudgetState())
    assert result.standalone_question == q


async def test_reference_cannot_add_an_unsupported_deadline():
    result = await understand_question('它能退吗？', history=[HumanMessage(content='订单1001', id='a')],
        gateway=gateway('订单1001签收7天能退吗？', 'resolved', '1001', 'a'), budget=BudgetState())
    assert result.standalone_question == '它能退吗？'


async def test_two_orders_in_source_do_not_justify_guessing_recent_one():
    result = await understand_question('它能退吗？', history=[HumanMessage(content='订单1001和订单1002', id='a')],
        gateway=gateway('订单1002能退吗？', 'resolved', '1002', 'a'), budget=BudgetState())
    assert result.order_id is None


async def test_natural_order_number_source_is_accepted():
    result = await understand_question('这单在哪？', history=[HumanMessage(content='当前讨论的订单是1002。', id='a')],
        gateway=gateway('订单1002在哪里？', 'resolved', '1002', 'a'), budget=BudgetState())
    assert result.order_id == '1002'
    assert result.standalone_question == '订单1002在哪里？'


async def test_explicit_order_number_with_label_and_spaces_is_accepted():
    result = await understand_question('订单号： 1001能退吗？', history=[],
        gateway=gateway('订单号： 1001能退吗？', 'unchanged', '1001'), budget=BudgetState())
    assert result.order_id == '1001'


async def test_compact_order_list_is_still_ambiguous():
    result = await understand_question('它能退吗？', history=[HumanMessage(content='订单1001和1002', id='a')],
        gateway=gateway('订单1001能退吗？', 'resolved', '1001', 'a'), budget=BudgetState())
    assert result.order_id is None


async def test_orders_across_separate_history_messages_remain_ambiguous():
    history=[HumanMessage(content='查订单1001',id='a'),HumanMessage(content='查订单1002',id='b')]
    result=await understand_question('它能退吗？',history=history,
        gateway=gateway('订单1002能退吗？','resolved','1002','b'),budget=BudgetState())
    assert result.order_id is None


async def test_current_explicit_order_cannot_be_replaced_by_a_historical_one():
    result=await understand_question('订单1002能退吗？',history=[HumanMessage(content='订单1001',id='a')],
        gateway=gateway('订单1002能退吗？','unchanged','1001','a'),budget=BudgetState())
    assert result.order_id != '1001'
