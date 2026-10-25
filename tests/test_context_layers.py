from copy import deepcopy
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from app.context.contracts import ContextSnapshot, ContextBudget
from app.context.layers import build_layered_view, summary_batch


def budget(n):
    return ContextBudget(18000, 2000, 1500, 1000, 2000, 850, 5000, n*10//7, n, 100)


def fixture():
    messages = [HumanMessage('订单ORD-1001的物流没到', id='u1'),
                AIMessage('答'*100, id='a1'), HumanMessage('继续查', id='u2'),
                AIMessage('', id='c2', tool_calls=[dict(name='query_order',args={},id='call')]),
                ToolMessage('工具大块'*800, tool_call_id='call', id='t2'),
                AIMessage('原文不改', id='a2'), HumanMessage('现在问题', id='u3')]
    refs = {m.id: dict(db_id=d, turn_from_msg_id=t) for m,d,t in zip(messages,
        [10,14,21,None,None,30,41], [10,10,21,21,21,21,41])}
    return messages, refs


def test_layer1_is_byte_identical_and_state_unchanged():
    messages, refs = fixture()
    before = deepcopy(messages)
    view = build_layered_view(messages, refs=refs, snapshot=ContextSnapshot(1,None,None,[]),
        current_turn_from_msg_id=41, budget=budget(20000))
    assert view.layer1 == messages[:-1] and not view.layer2
    assert messages == before and view.current == messages[-1:]


def test_layer2_preserves_user_and_first_60_assistant_chars():
    messages, refs = fixture()
    view = build_layered_view(messages, refs=refs, snapshot=ContextSnapshot(1,None,None,[]),
        current_turn_from_msg_id=41, budget=budget(1))
    assert view.layer1 == [] and view.layer1_from_msg_id == 41
    assert view.layer2[0].content == messages[0].content
    assert view.layer2[1].content == '答'*60 + '…[已截短]'
    batch = summary_batch(view, messages=messages, refs=refs, snapshot=ContextSnapshot(1,None,None,[]))
    assert (batch.from_msg_id, batch.upto_msg_id, batch.captured_layer1_from) == (10,30,41)


def test_layer2_keeps_complete_tool_pairs_with_marker():
    messages, refs = fixture()
    view = build_layered_view(messages, refs=refs, snapshot=ContextSnapshot(1,None,41,[]),
        current_turn_from_msg_id=41, budget=budget(20000))
    assert not view.layer1  # no automatic promotion
    tool = next(m for m in view.layer2 if isinstance(m, ToolMessage))
    assert 'query_order' in tool.content and 'call' in tool.content
    assert len(tool.content) < 100 and view.layer2_tokens < 500


def test_orphans_filtered_without_losing_interrupted_user():
    messages = [HumanMessage('上一轮原话',id='a'),AIMessage('',id='b',tool_calls=[dict(name='query_order',args={},id='x')]),
                HumanMessage('本轮',id='c')]
    refs = {'a':dict(db_id=11,turn_from_msg_id=11),'b':dict(db_id=None,turn_from_msg_id=11),
            'c':dict(db_id=22,turn_from_msg_id=22)}
    view = build_layered_view(messages,refs=refs,snapshot=ContextSnapshot(1,None,None,[]),
        current_turn_from_msg_id=22,budget=budget(1000))
    assert view.layer1 == messages[:1]


def test_empty_history_current_never_demoted():
    view = build_layered_view([],refs={},snapshot=ContextSnapshot(1,None,None,[]),
        current_turn_from_msg_id=15,budget=budget(1))
    assert not view.layer1 and not view.layer2 and view.layer1_from_msg_id == 15
