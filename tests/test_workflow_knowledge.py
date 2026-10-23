import pytest
from app.kb.contracts import Hit, QueryPlan, RetrievalResult
from app.workflow.contracts import BudgetState
from app.workflow.knowledge import evidence_gate, retrieve_knowledge, dump_retrieval, load_retrieval
from app.workflow.models import ModelGateway
from tests.test_workflow_models import Provider


def result(score=None, hits=True):
    plan = QueryPlan(raw_query='退货', normalized_query='退货', lexical_query='退货')
    rows = (Hit(1, .8, '政策', '退货', '规则', '文档章节', '退货规则', score_type='rerank', rerank_score=score),) if hits else ()
    return RetrievalResult(plan, 'hybrid_rerank', rows)


def test_gate_empty_and_low_score(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('app.kb.answering.get_settings', lambda: SimpleNamespace(rerank_min_score=.25))
    assert evidence_gate(result(hits=False))
    assert evidence_gate(result(.1))
    assert evidence_gate(result(.25)) == ''


def test_gate_nan_rejected():
    assert evidence_gate(result(float('nan')))


async def test_understood_query_is_not_rewritten_again():
    provider = Provider()
    provider.parsed = {'normalized_query': '退货政策', 'intent': 'knowledge', 'reason': ''}
    budget = BudgetState()
    def retrieve(question, *, plan):
        assert plan.normalized_query == '退货政策'
        return result(.8)
    await retrieve_knowledge('退货政策', gateway=ModelGateway(provider), budget=budget, retrieve=retrieve)
    assert budget.charged_tokens == 0
    assert provider.calls == []


async def test_retrieval_service_failure_is_not_knowledge_gap():
    provider = Provider()
    provider.parsed = {'normalized_query': '退货', 'intent': 'knowledge'}
    def broken(*args, **kwargs):
        raise ConnectionError('test outage')
    with pytest.raises(ConnectionError):
        await retrieve_knowledge('退货', gateway=ModelGateway(provider), budget=BudgetState(), retrieve=broken)


def test_evidence_roundtrip_preserves_score_and_source():
    restored = load_retrieval(dump_retrieval(result(.8)))
    assert restored.hits[0].rerank_score == .8
    assert restored.hits[0].section_path == '退货规则'
    assert restored.plan.raw_query == '退货'
