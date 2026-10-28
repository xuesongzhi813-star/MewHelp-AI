from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

import app.chat_service as chat
from app.config import get_settings
from app.executor import ToolExecutionResult
from app.kb.contracts import AnswerDecision, Hit, QueryPlan, RetrievalResult
from app.kb.answering import REFUSAL_TEXT
from tests.test_chat_service import FakeChatModel, FakeStore, collect, parse_sse, tool_call_chunks
from tests.test_low_confidence import lcq_tx  # noqa: F401 - 外层事务回滚


class AnswerModel:
    def __init__(self, decision=None):
        self.decision = decision or AnswerDecision(can_answer=True, reason='证据充足', answer='运费10元。[1]', used_citation_numbers=[1])
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.decision


@pytest.fixture
def grounded(monkeypatch):
    settings = get_settings().model_copy(update={'kb_corpus_version': 'ch04'})
    monkeypatch.setattr(chat, 'get_settings', lambda: settings)
    raw = '用户完整原话：新疆实付99元运费到底多少'
    plan = QueryPlan(raw_query=raw, normalized_query=raw, lexical_query=raw)
    hit = Hit(42, .95, '物流', '运费多少', '普通地区运费10元。', '文档章节', '开票配送 > 运费',
              score_type='rerank', rerank_score=.95)
    env = SimpleNamespace(raw=raw, plan=plan, result=RetrievalResult(plan, 'hybrid_rerank', (hit,), (42,)),
                          executed=[], pool=[], planned=[])
    def planner(raw):
        env.planned.append(raw)
        return env.plan
    def execute(name, args, **kwargs):
        env.executed.append((name, args, kwargs))
        return ToolExecutionResult('检索工具文本', env.result if name == 'query_faq' else None)
    monkeypatch.setattr(chat, 'build_query_plan', planner, raising=False)
    monkeypatch.setattr(chat, 'execute_tool_result', execute, raising=False)
    monkeypatch.setattr(chat, 'record_low_confidence', lambda *args: env.pool.append(args) or 1, raising=False)
    return env


def route_faq(prefix='未经验证的草稿：保证明天到账。'):
    return FakeChatModel([[AIMessageChunk(content=prefix),
        *tool_call_chunks('query_faq', '{"keyword":"模型缩写"}', 'call-1')]])


async def run(env, model=None, answer_model=None, store=None):
    return parse_sse(await collect(chat.stream_chat('s', env.raw, store=store or FakeStore(conversation_id=77),
                    model=model or route_faq(), answer_model=answer_model or AnswerModel())))


async def test_citations_before_tokens_and_unverified_route_draft_never_published(grounded):
    store = FakeStore(conversation_id=77)
    events = await run(grounded, store=store)
    types = [e['type'] for e in events]
    assert types.index('citations') < types.index('token')
    assert types[-1] == 'done'
    assert '未经验证' not in str(events) and '保证明天到账' not in str(events)
    citation = next(e for e in events if e['type'] == 'citations')['items'][0]
    assert citation == {'number': 1, 'chunk_id': 42, 'section_path': '开票配送 > 运费',
                        'text': '普通地区运费10元。', 'original_url': '/kb?chunk_id=42'}
    assert grounded.executed[0][2]['context'] == {'conversation_id': 77, 'query_plan': grounded.plan}
    assert grounded.planned == [grounded.raw] and grounded.pool == []
    assert [type(m) for _, m in store.appended] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert not store.appended[1][1].content and store.appended[-1][1].content == '运费10元。[1]'


async def test_knowledge_without_tool_forces_exactly_one_faq(grounded):
    model = FakeChatModel([[AIMessageChunk(content='猜测答案，没有工具')]])
    events = await run(grounded, model=model)
    assert [item[0] for item in grounded.executed] == ['query_faq']
    assert events[-1]['type'] == 'done' and '猜测' not in str(events)
    assert len(model.received) == 1


async def test_knowledge_product_size_uses_faq_before_executing_wrong_router_tool(grounded):
    grounded.raw = 'MH-DJ101的胸围是多少'
    grounded.plan = grounded.plan.model_copy(update={'raw_query': grounded.raw,
        'normalized_query': grounded.raw, 'lexical_query': grounded.raw, 'intent': 'knowledge'})
    from dataclasses import replace
    grounded.result = replace(grounded.result, plan=grounded.plan)
    model = FakeChatModel([[*tool_call_chunks('query_product', '{"product_id":"MH-DJ101"}', 'call-product')]])
    events = await run(grounded, model=model)
    assert [item[0] for item in grounded.executed] == ['query_faq']
    assert grounded.pool == []
    assert any(e['type'] == 'citations' for e in events)
    assert len(model.received) == 1


async def test_business_with_policy_question_cannot_publish_unreferenced_policy(grounded):
    grounded.plan = grounded.plan.model_copy(update={'intent':'business','requires_knowledge':True})
    model = FakeChatModel([[*tool_call_chunks('query_order', '{"order_id":"ORD202610200001"}', 'call-order')],
                           [AIMessageChunk(content='签收后7天内都可以退货')]])
    events = await run(grounded, model=model)
    assert ''.join(e['content'] for e in events if e['type']=='token') == REFUSAL_TEXT
    assert len(grounded.pool) == 1 and grounded.pool[0][2] == 'self_check'
    assert len(grounded.executed) == 1 and len(model.received) == 1


async def test_partial_evidence_self_check_refusal_records_original_once(grounded):
    answer = AnswerModel(AnswerDecision(can_answer=False, reason='没有新疆费用', answer='不能发布的草稿', used_citation_numbers=[]))
    events = await run(grounded, answer_model=answer)
    assert grounded.pool == [(grounded.raw, 77, 'self_check', '没有新疆费用')]
    assert not any(e['type'] == 'citations' for e in events)
    assert ''.join(e['content'] for e in events if e['type'] == 'token') == REFUSAL_TEXT
    assert '不能发布' not in str(events)


@pytest.mark.parametrize('low_score', [None, .1])
async def test_no_hits_or_low_score_use_retrieval_pool_without_answer_model(grounded, low_score):
    if low_score is None:
        grounded.result = RetrievalResult(grounded.plan, 'hybrid_rerank')
    else:
        from dataclasses import replace
        hit = replace(grounded.result.hits[0], score=low_score, rerank_score=low_score)
        grounded.result = RetrievalResult(grounded.plan, 'hybrid_rerank', (hit,))
    model = AnswerModel()
    events = await run(grounded, answer_model=model)
    assert len(grounded.pool) == 1 and grounded.pool[0][:3] == (grounded.raw, 77, 'retrieval_low_conf')
    assert model.calls == [] and events[-1]['type'] == 'done'


async def test_pool_failure_keeps_refusal_and_reports_unsaved(grounded, monkeypatch):
    grounded.result = RetrievalResult(grounded.plan, 'hybrid_rerank')
    def fail(*args):
        raise RuntimeError('private DB details')
    monkeypatch.setattr(chat, 'record_low_confidence', fail)
    events = await run(grounded)
    assert events[-1]['type'] == 'done'
    assert any(e['type'] == 'collection_status' and e['saved'] is False for e in events)
    assert ''.join(e['content'] for e in events if e['type'] == 'token') == REFUSAL_TEXT
    assert not any(e['type'] == 'citations' for e in events)
    assert '已记录' not in str(events) and 'private' not in str(events)


async def test_retrieval_service_failure_does_not_pollute_pool(grounded, monkeypatch):
    monkeypatch.setattr(chat, 'execute_tool_result', lambda *a, **kw: ToolExecutionResult('服务失败', status='error'))
    events = await run(grounded)
    assert grounded.pool == [] and events[-1]['type'] == 'error'
    assert not any(e['type'] == 'token' for e in events)


async def test_answer_provider_failure_does_not_pollute_pool(grounded):
    class Broken:
        async def ainvoke(self, messages):
            raise RuntimeError('private model info')
    events = await run(grounded, answer_model=Broken())
    assert grounded.pool == [] and events[-1]['type'] == 'error'
    assert 'private' not in str(events)


async def test_unknown_citation_is_refused_and_only_final_used_citations_published(grounded):
    answer = AnswerModel(AnswerDecision(can_answer=True, reason='可答', answer='猜测。[2]', used_citation_numbers=[2]))
    events = await run(grounded, answer_model=answer)
    assert len(grounded.pool) == 1 and grounded.pool[0][2] == 'self_check'
    assert not any(e['type'] == 'citations' for e in events)


async def test_business_branch_never_adds_second_tool_and_blocks_promises_before_token(grounded):
    grounded.plan = grounded.plan.model_copy(update={'intent': 'business'})
    model = FakeChatModel([
        [AIMessageChunk(content='先猜一段'), *tool_call_chunks('query_logistics', '{"order_id":"X"}', 'call-x')],
        [AIMessageChunk(content='您的退款'), AIMessageChunk(content='保证3天到账。')],
    ])
    events = await run(grounded, model=model)
    assert [item[0] for item in grounded.executed] == ['query_logistics']
    assert '保证3天到账' not in str(events) and '先猜一段' not in str(events)
    assert not any(e['type'] == 'citations' for e in events)
    assert model.bind_tools_calls[-1]['kwargs']['tool_choice'] == 'none'
    assert grounded.pool[0][2] == 'self_check'


async def test_wrong_business_tool_for_knowledge_is_replaced_before_execution(grounded):
    model = FakeChatModel([
        [*tool_call_chunks('query_order', '{"order_id":"X"}', 'call-x')],
        [AIMessageChunk(content='订单已发货，运费我猜10元')],
    ])
    events = await run(grounded, model=model)
    assert [item[0] for item in grounded.executed] == ['query_faq']
    assert '我猜' not in str(events)
    assert grounded.pool == []
    assert len(model.received) == 1


async def test_chat_without_citation_completes_and_does_not_write_pool(grounded):
    grounded.plan = grounded.plan.model_copy(update={'intent': 'chat'})
    model = FakeChatModel([[AIMessageChunk(content='你好'), AIMessageChunk(content='呀')]])
    events = await run(grounded, model=model)
    assert ''.join(e['content'] for e in events if e['type'] == 'token') == '你好呀'
    assert grounded.pool == grounded.executed == [] and events[-1]['type'] == 'done'


async def test_disconnect_after_tool_end_does_not_run_answer_or_write_pool(grounded):
    answer_model = AnswerModel()
    agen = chat.stream_chat('s', grounded.raw, store=FakeStore(), model=route_faq(), answer_model=answer_model)
    async for frame in agen:
        event = parse_sse([frame])[0]
        if event.get('phase') == 'end':
            break
    await agen.aclose()
    assert answer_model.calls == [] and grounded.pool == []


async def test_api_dependencies_publish_grounded_citations(client, grounded):
    from app.main import app
    from app import dependencies
    previous = app.state.session_store
    try:
        app.state.session_store = FakeStore(conversation_id=77)
        app.dependency_overrides[dependencies.get_chat_model] = route_faq
        app.dependency_overrides[dependencies.get_answer_model] = AnswerModel
        response = await client.post('/chat', json={'session_id': 's', 'message': grounded.raw})
        assert response.status_code == 200
        events = parse_sse([line + '\n\n' for line in response.text.split('\n\n') if line])
        assert next(e for e in events if e['type'] == 'citations')['items'][0]['chunk_id'] == 42
        assert events[-1]['type'] == 'done'
    finally:
        app.state.session_store = previous


async def test_refusal_pool_binds_real_mysql_conversation_and_persists_once(grounded, lcq_tx, monkeypatch):
    import uuid
    from sqlalchemy import select
    import app.session_store as sessions
    from app.kb.low_confidence import record_low_confidence
    from app.models import LowConfidenceQuestion
    monkeypatch.setattr(sessions, 'get_session_local', lambda: lcq_tx)
    monkeypatch.setattr(chat, 'record_low_confidence', record_low_confidence)
    session_id = 'ch04-chat-test-' + uuid.uuid4().hex
    store = sessions.MySQLSessionStore()
    grounded.result = RetrievalResult(grounded.plan, 'hybrid_rerank')
    frames = await collect(chat.stream_chat(session_id, grounded.raw, store=store,
                           model=route_faq(), answer_model=AnswerModel()))
    assert parse_sse(frames)[-1]['type'] == 'done'
    conversation_id = store.get_or_create_conversation(session_id)
    with lcq_tx() as db:
        rows = list(db.scalars(select(LowConfidenceQuestion).where(
            LowConfidenceQuestion.conversation_id == conversation_id)))
        assert len(rows) == 1
        assert rows[0].raw_question == grounded.raw and rows[0].source == 'retrieval_low_conf'
        assert rows[0].reason and rows[0].created_at
        assert rows[0].retrieved_chunks['snapshot_status'] == 'no_hits'
    messages = store.get_history(session_id)
    assert [type(m) for m in messages] == [HumanMessage, AIMessage, ToolMessage, AIMessage]
    assert messages[-1].content == REFUSAL_TEXT


async def test_only_used_evidence_is_sent_and_numbering_does_not_shift(grounded):
    from dataclasses import replace
    second = replace(grounded.result.hits[0], chunk_id=43, answer='第二块原文', section_path='第二章节')
    grounded.result = RetrievalResult(grounded.plan, 'hybrid_rerank', (*grounded.result.hits, second))
    answer = AnswerModel(AnswerDecision(can_answer=True, reason='第二块支持', answer='第二块原文。[2]', used_citation_numbers=[2]))
    events = await run(grounded, answer_model=answer)
    citation = next(e for e in events if e['type'] == 'citations')['items']
    assert citation == [{'number': 2, 'chunk_id': 43, 'section_path': '第二章节', 'text': '第二块原文',
                         'original_url': '/kb?chunk_id=43'}]
