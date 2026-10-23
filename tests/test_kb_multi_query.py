from dataclasses import replace
import pytest
from app.kb.contracts import Hit, QueryPlan, RetrievalResult
from app.kb.multi_query import QueryExpansion, retrieve_policy_queries
from app.kb.multi_query import expand_policy_queries
from app.workflow.contracts import BudgetState


def hit(key):
    return Hit(key, 9999, 'MewHelp 古着退货政策 > 条件', '能退吗', '政策正文', '文档章节', 'MewHelp 古着退货政策 > 条件')


def test_multi_query_dedupes_and_reranks_against_original(monkeypatch):
    monkeypatch.setenv('KNOWLEDGE_STRATEGY', 'hybrid_rerank')
    monkeypatch.setenv('KB_CORPUS_VERSION', 'ch04')
    from app.config import get_settings
    get_settings.cache_clear()
    seen = []
    def retrieve(query, *, strategy, plan):
        seen.append((query, strategy))
        return RetrievalResult(plan, strategy, (hit(1), hit(2)))
    def rerank(query, documents, *, top_n):
        assert query == '这一单能退吗'
        assert len(documents) == 2
        return [(1, .9), (0, .8)]
    plan = QueryPlan(raw_query='这一单能退吗', normalized_query='这一单能退吗', lexical_query='这一单能退吗')
    try:
        result = retrieve_policy_queries(plan.raw_query, ['这一单能退吗', '退货条件'], plan=plan, retrieve=retrieve, rerank=rerank)
        assert [h.chunk_id for h in result.hits] == [2, 1]
        assert result.hits[0].rerank_score == .9
        assert len(result.candidate_ids) == 2
        assert len(seen) == 4
        assert all(strategy == 'hybrid' for _, strategy in seen)
    finally:
        get_settings.cache_clear()


def test_one_query_failure_is_not_hidden_by_other_hits():
    plan = QueryPlan(raw_query='能退吗', normalized_query='能退吗', lexical_query='能退吗')
    def broken(*args, **kwargs):
        raise ConnectionError('synthetic outage')
    with pytest.raises(ConnectionError):
        retrieve_policy_queries('能退吗', ['能退吗'], plan=plan, retrieve=broken, rerank=lambda *a, **k: [])


@pytest.mark.parametrize('value', [{'queries':['x','y'], 'reason':'extra'}, {'queries':['x']}, {'queries':['x','']}])
def test_expansion_shape_is_strict(value):
    with pytest.raises(ValueError):
        QueryExpansion.model_validate(value)


async def test_expansion_never_adds_an_unsupported_deadline():
    class Gateway:
        calls = 0
        async def expand(self, messages, budget):
            self.calls += 1
            return {'queries': ['订单1001签收7天退货条件', '订单1001退货流程']}
    gateway = Gateway()
    assert await expand_policy_queries('订单1001能退吗', order={'order_id':'1001'}, gateway=gateway, budget=BudgetState()) == ['订单1001能退吗']
    assert gateway.calls == 2


async def test_duplicate_expansions_fall_back_after_one_repair():
    class Gateway:
        calls = 0
        async def expand(self, messages, budget):
            self.calls += 1
            return {'queries': ['退款条件', '退款条件']}
    gateway = Gateway()
    assert await expand_policy_queries('能退吗', order={}, gateway=gateway, budget=BudgetState()) == ['能退吗']
    assert gateway.calls == 2


def test_source_filter_uses_parameters_for_title_and_descendants():
    from app.kb.filters import compile_filter
    from app.kb.contracts import RetrievalFilters
    expression, values = compile_filter(RetrievalFilters(source_title='MewHelp 古着退货政策'))
    assert 'MewHelp' not in expression
    assert values['source_prefix'] == 'MewHelp 古着退货政策 > %'
