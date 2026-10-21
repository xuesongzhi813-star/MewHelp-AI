import pytest

from app.kb import answering
from app.kb.contracts import AnswerDecision, Evidence, Hit, QueryPlan, RetrievalResult


def hit(index=1, score=.9, answer='运费10元。'):
    return Hit(index, score, '物流', '运费多少', answer, '文档章节', '开票配送 > 运费',
               score_type='rerank', rerank_score=score)


class Model:
    def __init__(self, decision):
        self.decision, self.calls = decision, []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.decision


class SequenceModel(Model):
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.responses.pop(0)


async def test_uncited_intro_gets_one_format_repair_without_losing_valid_return_steps():
    plan = QueryPlan(raw_query='退货流程是什么', normalized_query='退货流程是什么', lexical_query='退货流程')
    raw = '退货流程如下：\n\n1. 在我的订单申请售后。[1]'
    model = SequenceModel(decision(raw), decision('退货流程如下：[1]\n\n1. 在我的订单申请售后。[1]'))
    result = await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(answer='在我的订单申请售后。'),)), model=model)
    assert result.can_answer and result.used_citation_numbers == [1]
    assert len(model.calls) == 2
    assert '退货流程如下' in result.answer


@pytest.mark.parametrize('repaired', ['胸围120厘米。[1]', '胸围110厘米。[2]', '胸围110厘米。'])
async def test_format_repair_cannot_change_facts_or_bypass_citation_guard(repaired):
    plan = QueryPlan(raw_query='胸围', normalized_query='胸围', lexical_query='胸围')
    model = SequenceModel(decision('胸围110厘米。', ()), decision(repaired, (2,) if '[2]' in repaired else (1,)))
    result = await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(answer='胸围110厘米。'),)), model=model)
    assert not result.can_answer and not result.answer
    assert len(model.calls) == 2


async def test_invalid_citation_format_never_gets_more_than_one_repair():
    plan = QueryPlan(raw_query='运费', normalized_query='运费', lexical_query='运费')
    model = SequenceModel(decision('运费10元。', ()), decision('运费10元。', ()))
    result = await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(),)), model=model)
    assert not result.can_answer and len(model.calls) == 2


async def test_format_repair_does_not_rescue_an_unsafe_promise():
    plan = QueryPlan(raw_query='到账', normalized_query='到账', lexical_query='到账')
    model = SequenceModel(decision('保证3天到账。', ()))
    result = await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(),)), model=model)
    assert not result.can_answer and len(model.calls) == 1


async def test_format_repair_uses_same_total_generation_budget(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    monkeypatch.setattr(answering, 'get_settings', lambda: SimpleNamespace(answer_timeout=.3))
    class SlowRepair:
        calls = 0
        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(.05)
                return decision('运费10元。', ())
            await asyncio.Event().wait()
    model = SlowRepair()
    plan = QueryPlan(raw_query='运费', normalized_query='运费', lexical_query='运费')
    with pytest.raises(answering.AnswerServiceError):
        await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(),)), model=model)
    assert model.calls == 2


def decision(answer='普通地区运费10元。[1]', citations=(1,), **kw):
    return AnswerDecision(can_answer=kw.get('can_answer', True), reason=kw.get('reason', '证据充足'),
                          answer=answer, used_citation_numbers=list(citations))


def test_order_keeps_citation_numbers():
    evidence = answering.make_evidence([hit(n) for n in range(1, 11)])
    reordered = answering.reorder_evidence(evidence)
    assert [e.number for e in reordered] == [1, 3, 5, 7, 9, 10, 8, 6, 4, 2]
    assert reordered[0].hit.chunk_id == 1 and reordered[-1].hit.chunk_id == 2
    assert [e.number for e in answering.reorder_evidence(evidence[:5])] == [1, 3, 5, 4, 2]


@pytest.mark.parametrize('hits', [(), (hit(score=.1),)])
async def test_no_hit_or_low_rerank_score_short_circuits_model(hits):
    plan = QueryPlan(raw_query='运费', normalized_query='运费', lexical_query='运费')
    model = Model(decision())
    result = await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid_rerank', hits), model=model)
    assert not result.can_answer and not result.answer and model.calls == []


async def test_fair_comparison_can_disable_score_gate_and_rrf_never_uses_it():
    plan = QueryPlan(raw_query='运费', normalized_query='运费', lexical_query='运费')
    model = Model(decision())
    low = (hit(score=.01),)
    assert (await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid_rerank', low), model=model, apply_score_gate=False)).can_answer
    assert (await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', low), model=model)).can_answer
    assert len(model.calls) == 2


@pytest.mark.parametrize(('answer', 'citations'), [
    ('运费10元。[2]', (2,)), ('运费10元。', (1,)), ('运费10元。[1]', ()),
    ('运费10元。[1]\n\n明天保证收到。', (1,)), ('运费10元。[1]\n\n积分有效期12个月。', (1,)),
    ('运费10元。[1]', (1, 1)),
])
def test_unknown_or_missing_citation_blocks_draft(answer, citations):
    result = answering.validate_answer(decision(answer, citations), [Evidence(1, hit())])
    assert not result.can_answer and not result.answer and not result.used_citation_numbers


@pytest.mark.parametrize(('text', 'blocked'), [
    ('我保证3天到账。[1]', True), ('退款3个工作日到账。[1]', True),
    ('我不承诺3天到账。[1]', False), ('不能保证明天到账。[1]', False),
    ('不能保证3天到账，但保证明天到账。[1]', True),
    ('一定明天送达。[1]', True), ('预计明天必达。[1]', True),
    ('不能保证物流必达日。[1]', False), ('为您提供免费维修。[1]', True),
    ('免费维修并非本平台已确认权益。[1]', False),
    ('我们将赔付30元。[1]', True), ('是否赔付需人工核实。[1]', False),
    ('保证正品且必然合身。[1]', True), ('不能保证真伪或必然合身。[1]', False),
    ('MH-JK501不能保证防雨。[1]', False), ('现货通常付款后24小时发货。[1]', False),
])
def test_payment_eta_promise_blocked_but_negated_promise_allowed(text, blocked):
    assert answering.has_forbidden_promise(text) is blocked


async def test_missing_condition_or_conflict_refuses_without_draft():
    plan = QueryPlan(raw_query='新疆运费', normalized_query='新疆运费', lexical_query='新疆运费')
    model = Model(decision('可能10元。[1]', (1,), can_answer=False, reason='缺少新疆费用'))
    result = await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(),)), model=model)
    assert not result.can_answer and not result.answer and result.reason == '缺少新疆费用'


async def test_model_service_failure_is_not_knowledge_refusal():
    class Broken:
        async def ainvoke(self, messages):
            raise RuntimeError('private provider message')
    plan = QueryPlan(raw_query='运费', normalized_query='运费', lexical_query='运费')
    with pytest.raises(answering.AnswerServiceError) as caught:
        await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(),)), model=Broken())
    assert 'private' not in str(caught.value)


async def test_invalid_model_structure_refuses_without_releasing_draft():
    plan = QueryPlan(raw_query='运费', normalized_query='运费', lexical_query='运费')
    model = Model({'can_answer': True, 'answer': '无引用的草稿'})
    result = await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(),)), model=model)
    assert not result.can_answer and not result.answer


async def test_evidence_prompt_preserves_id_path_text_and_original_query():
    plan = QueryPlan(raw_query='新疆99元包邮吗', normalized_query='新疆运费', lexical_query='新疆运费')
    model = Model(decision())
    await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(),)), model=model)
    prompt = model.calls[0][-1].content
    assert '新疆99元包邮吗' in prompt and '开票配送 > 运费' in prompt
    assert 'chunk_id' in prompt and '运费10元' in prompt and '[1]' in prompt


async def test_answer_timeout_is_service_error(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    monkeypatch.setattr(answering, 'get_settings', lambda: SimpleNamespace(answer_timeout=.01))
    class Slow:
        async def ainvoke(self, messages):
            await asyncio.Event().wait()
    plan = QueryPlan(raw_query='运费', normalized_query='运费', lexical_query='运费')
    with pytest.raises(answering.AnswerServiceError):
        await answering.generate_answer(plan, RetrievalResult(plan, 'hybrid', (hit(),)), model=Slow())


def test_answer_model_no_sdk_retries_and_parse_errors_as_data(monkeypatch):
    import app.llm as llm
    from app.config import get_settings
    captured = {}
    class Chat:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def with_structured_output(self, schema, **kwargs):
            captured.update(kwargs)
            return self
    monkeypatch.setattr(llm, 'ChatOpenAI', Chat)
    llm.build_answer_model(get_settings())
    assert captured['timeout'] == 45 and captured['max_retries'] == 0
    assert captured['include_raw'] is True and captured['method'] == 'function_calling'
