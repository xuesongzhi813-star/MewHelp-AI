import threading
from types import SimpleNamespace

import pytest

from app.kb import query
from app.kb.contracts import RetrievalFilters


class FakeModel:
    def __init__(self, text, intent='knowledge'):
        self.text, self.intent, self.messages = text, intent, []

    def invoke(self, messages):
        self.messages = messages
        return query.QueryRewrite(normalized_query=self.text, intent=self.intent, reason='测试改写')


@pytest.mark.parametrize('rewritten', [
    '不要衬衫，签收后7天，99元的衣服能退吗',
    'MH-DJ101不是衬衫，付款后7天，99元的衣服能退吗',
    'MH-DJ101签收后7天，99元的衣服能退吗',
    'MH-DJ101不要衬衫，签收后7天，98元的衣服能退吗',
    'MH-DJ102不要衬衫，签收后7天，99元的衣服能退吗',
])
def test_rewrite_preserves_sku_negation_amount_and_deadline_origin(rewritten):
    raw = 'MH-DJ101不要衬衫，签收后7天，99元的衣服能退吗'
    plan = query.build_query_plan(raw, model=FakeModel(rewritten))
    assert plan.rewrite_fallback and plan.normalized_query == query.normalize_query(raw)
    assert plan.raw_query == raw


def test_valid_rewrite_only_lexical_gets_synonyms():
    plan = query.build_query_plan('寄来要掏多少邮费', model=FakeModel('运费如何计算'))
    assert not plan.rewrite_fallback
    assert plan.normalized_query == '运费如何计算'
    assert '邮费' in plan.lexical_query and '运费' in plan.lexical_query
    assert plan.intent == 'knowledge'


def test_rewrite_failure_does_not_drop_explicit_filter():
    filters = RetrievalFilters(product_category='衬衫', include_general=True)
    class Broken:
        def invoke(self, messages):
            raise RuntimeError('不要把远端的凭据或异常正文写进reason')
    plan = query.build_query_plan('如何保养', filters=filters, model=Broken())
    assert plan.rewrite_fallback and plan.filters == filters
    assert '凭据' not in plan.rewrite_reason


def test_pronoun_does_not_expand_using_history():
    raw = '那个大一点的呢'
    fake = FakeModel('MH-DJ102的胸围是多少')
    plan = query.build_query_plan(raw, model=fake)
    assert plan.normalized_query == raw and plan.rewrite_fallback
    assert fake.messages[-1].content == raw
    assert len(fake.messages) == 2


@pytest.mark.parametrize(('raw', 'normalized'), [
    ('MH-DJ101材质', 'MH-DJ102材质'), ('签收后7天内能退吗', '签收后7天后能退吗'),
    ('99元能包邮吗', '99天能包邮吗'), ('不是纯棉吗', '是纯棉吗'),
    ('2件能退吗', '1件能退吗'), ('明天到账吗', '到账吗'),
    ('ORD202610200001到哪了', 'ORD202610200002到哪了'),
    ('MH-DJ101纯棉吗', 'MH-DJ101不是纯棉吗'), ('7天能退吗', '签收后7天能退吗'),
])
def test_validate_rewrite_rejects_changed_constraints(raw, normalized):
    assert not query.validate_rewrite(raw, normalized)


def test_nfkc_whitespace_and_sku_case_normalization():
    raw = '  ｍｈ－ｄｊ１０１  胸围？ '
    plan = query.build_query_plan(raw, model=FakeModel('MH-DJ101的胸围是多少'))
    assert plan.raw_query == raw
    assert plan.normalized_query == 'MH-DJ101的胸围是多少'
    assert not plan.rewrite_fallback


def test_filter_only_explicit_category_or_known_sku():
    assert query.build_query_plan('MH-SH202啥料', model=FakeModel('MH-SH202的材质')).filters.product_category == '衬衫'
    assert query.build_query_plan('只看衬衫，咋洗', model=FakeModel('只看衬衫，如何洗涤')).filters.product_category == '衬衫'
    assert query.build_query_plan('不要衬衫，牛仔怎么洗', model=FakeModel('不要衬衫，牛仔如何洗涤')).filters.product_category is None
    assert query.build_query_plan('夏天穿什么', model=FakeModel('夏天穿什么衬衫')).filters.product_category is None
    # 显式空过滤也不被SKU推断覆盖。
    assert query.build_query_plan('MH-SH202啥料', filters=RetrievalFilters(), model=FakeModel('MH-SH202的材质')).filters == RetrievalFilters()


def test_sku_inference_keeps_general_return_policy_eligible():
    raw = 'MH-DJ101签收后7天内能退吗'
    plan = query.build_query_plan(raw, model=FakeModel(raw))
    assert plan.filters.product_category == '牛仔夹克'
    assert plan.filters.include_general


def test_order_number_does_not_remove_policy_evidence_requirement():
    raw = '我的ORD202610200001签收后7天内还能退货吗'
    plan = query.build_query_plan(raw, model=FakeModel(raw,'business'))
    assert plan.intent == 'business'
    assert plan.requires_knowledge


def test_timeout_returns_before_late_model_and_preserves_plan(monkeypatch):
    gate = threading.Event()
    class Slow:
        def invoke(self, messages):
            gate.wait(2)
            return query.QueryRewrite(normalized_query='已晚到的结果', intent='chat', reason='')
    monkeypatch.setattr(query, 'get_settings', lambda: SimpleNamespace(query_timeout=.01))
    try:
        plan = query.build_query_plan('运费如何计算', model=Slow())
        assert plan.rewrite_fallback and plan.normalized_query == '运费如何计算'
        assert plan.intent == 'knowledge'
    finally:
        gate.set()


@pytest.mark.parametrize(('raw', 'candidate_intent', 'expected'), [
    ('MH-DJ101胸围多少', 'chat', 'knowledge'), ('邮费是多少', 'chat', 'knowledge'),
    ('你好', 'chat', 'chat'), ('你好，怎么退货', 'chat', 'knowledge'),
    ('我的ORD202610200001到哪了', 'business', 'business'),
    ('我要找人工', 'business', 'business'),
])
def test_intent_cannot_bypass_knowledge_guard(raw, candidate_intent, expected):
    assert query.build_query_plan(raw, model=FakeModel(raw, candidate_intent)).intent == expected


def test_empty_query_short_circuits_model():
    class Never:
        def invoke(self, messages):
            pytest.fail('空白不应调用模型')
    assert query.build_query_plan('  ', model=Never()).normalized_query == ''


def test_query_model_has_bounded_timeout_and_no_sdk_retry(monkeypatch):
    import app.llm as llm
    from app.config import get_settings
    captured = {}
    class Chat:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        def with_structured_output(self, schema, **kwargs):
            captured['schema'] = schema
            captured['method'] = kwargs['method']
            return self
    monkeypatch.setattr(llm, 'ChatOpenAI', Chat)
    settings = get_settings()
    llm.build_query_model(settings)
    assert captured['timeout'] == settings.query_timeout
    assert captured['max_retries'] == 0
    assert captured['extra_body'] == settings.llm_extra_body
    assert captured['method'] == 'function_calling'
    assert captured['schema'] is query.QueryRewrite
