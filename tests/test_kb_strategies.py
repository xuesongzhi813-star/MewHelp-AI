from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.kb import retriever
from app.kb.contracts import QueryPlan, RetrievalFilters
from tests.test_kb_retriever import _stored


@pytest.fixture
def strategy_env(monkeypatch):
    from app.kb import migration
    settings = get_settings().model_copy(update={'kb_corpus_version': 'ch04', 'knowledge_strategy': 'hybrid_rerank'})
    monkeypatch.setattr(retriever, 'get_settings', lambda: settings)
    monkeypatch.setattr(migration, 'load_manifest', lambda: SimpleNamespace(phase='validated', legacy_excluded_ids=[]))
    calls = []
    def record(kind, result):
        def run(*args, **kw):
            calls.append((kind, args, kw))
            return result
        return run
    monkeypatch.setattr(retriever, 'embed_texts', record('embed', [[.1] * 1024]))
    monkeypatch.setattr(retriever.milvus, 'search_dense', record('dense', [('2', .1), ('1', .05)]))
    monkeypatch.setattr(retriever.milvus, 'search_bm25', record('bm25', [('1', 2.), ('2', 1.)]))
    monkeypatch.setattr(retriever.milvus, 'search_hybrid', record('hybrid', [('2', .03), ('1', .02), ('2', .01), ('999', .001)]))
    monkeypatch.setattr(retriever.store, 'fetch_by_ids', lambda ids: {i: _stored(i) for i in ids if i != 999})
    monkeypatch.setattr(retriever, 'rerank', record('rerank', [(1, .9), (0, .4)]), raising=False)
    plan = QueryPlan(raw_query='口语原话', normalized_query='标准问题', lexical_query='标准问题 同义词',
                     filters=RetrievalFilters(product_category='衬衫'))
    return plan, calls


@pytest.mark.parametrize('strategy', ['dense', 'bm25', 'hybrid', 'hybrid_rerank'])
def test_four_strategies_use_same_plan_and_bound_filters(strategy_env, monkeypatch, strategy):
    plan, calls = strategy_env
    monkeypatch.setattr(retriever, 'build_query_plan', lambda *a, **kw: pytest.fail('不得重复改写'), raising=False)
    result = retriever.retrieve_result(plan.raw_query, plan=plan, strategy=strategy)
    assert result.plan is plan and result.strategy == strategy
    assert result.candidate_ids == ((1, 2) if strategy == 'bm25' else (2, 1))
    assert all(hit.score_type == {'hybrid': 'fusion', 'hybrid_rerank': 'rerank'}.get(strategy, strategy) for hit in result.hits)
    search = next(call for call in calls if call[0] in ('dense', 'bm25', 'hybrid'))
    assert search[2]['filters'] is plan.filters
    assert search[2]['collection_name'] == 'knowledge_hybrid'
    if strategy == 'bm25':
        assert all(call[0] != 'embed' for call in calls)
        assert search[1][:2] == (plan.lexical_query, 50)
    else:
        assert len([c for c in calls if c[0] == 'embed']) == 1
        assert next(c for c in calls if c[0] == 'embed')[1] == ([plan.normalized_query],)
    if strategy == 'hybrid_rerank':
        assert [hit.chunk_id for hit in result.hits] == [1, 2]
        assert result.hits[0].fusion_score == .02 and result.hits[0].rerank_score == .9
        assert next(c for c in calls if c[0] == 'rerank')[2] == {'top_n': 10}
    else:
        assert all(c[0] != 'rerank' for c in calls)
        assert len(result.hits) == 2  # 不套用旧余弦0.5阈值


def test_no_plan_builds_once_with_explicit_filter(strategy_env, monkeypatch):
    plan, calls = strategy_env
    created = []
    monkeypatch.setattr(retriever, 'build_query_plan', lambda raw, **kw: created.append((raw, kw)) or plan, raising=False)
    retriever.retrieve_result('口语原话', strategy='bm25', filters=plan.filters)
    assert created == [('口语原话', {'filters': plan.filters})]


def test_empty_result_does_not_rerank(strategy_env, monkeypatch):
    plan, calls = strategy_env
    monkeypatch.setattr(retriever.milvus, 'search_hybrid', lambda *a, **kw: [])
    assert retriever.retrieve_result(plan.raw_query, plan=plan).hits == ()
    assert all(c[0] != 'rerank' for c in calls)


def test_empty_query_never_calls_planner_or_network(strategy_env, monkeypatch):
    monkeypatch.setattr(retriever, 'build_query_plan', lambda *a, **kw: pytest.fail('空白改写'), raising=False)
    assert retriever.retrieve_result(' ').hits == ()
    assert strategy_env[1] == []


def test_not_validated_manifest_blocks_search(strategy_env, monkeypatch):
    from app.kb import migration
    monkeypatch.setattr(migration, 'load_manifest', lambda: SimpleNamespace(phase='indexing'))
    with pytest.raises(ValueError, match='迁移'):
        retriever.retrieve_result('q', plan=strategy_env[0])
    assert strategy_env[1] == []


def test_rerank_failure_is_not_silent_strategy_fallback(strategy_env, monkeypatch):
    from app.kb.reranker import RerankError
    def fail(*a, **kw):
        raise RerankError('timeout')
    monkeypatch.setattr(retriever, 'rerank', fail)
    with pytest.raises(RerankError):
        retriever.retrieve_result('q', plan=strategy_env[0])


def test_hybrid_rerank_keeps_100_candidates_but_only_returns_top10(strategy_env, monkeypatch):
    plan, calls = strategy_env
    monkeypatch.setattr(retriever.milvus, 'search_hybrid', lambda *a, **kw: [(str(n), .03) for n in range(1, 101)])
    monkeypatch.setattr(retriever.store, 'fetch_by_ids', lambda ids: {n: _stored(n) for n in ids})
    monkeypatch.setattr(retriever, 'rerank', lambda query, docs, **kw: [(n, .99 - n * .01) for n in range(99, 89, -1)])
    result = retriever.retrieve_result('q', plan=plan)
    assert result.candidate_ids == tuple(range(1, 101))
    assert [hit.chunk_id for hit in result.hits] == list(range(100, 90, -1))


def test_legacy_dedup_api_uses_dense_cosine_even_when_online_is_reranked(strategy_env):
    plan, calls = strategy_env
    hits = retriever.search_hits('规格', top_k=3, min_score=.07)
    assert [h.chunk_id for h in hits] == [2]
    assert [c[0] for c in calls] == ['embed', 'dense']


def test_excluded_legacy_id_cannot_leak_from_corrupt_target(strategy_env, monkeypatch):
    from app.kb import migration
    monkeypatch.setattr(migration, 'load_manifest', lambda: SimpleNamespace(phase='validated', legacy_excluded_ids=[1]))
    result = retriever.retrieve_result('q', plan=strategy_env[0], strategy='bm25')
    assert result.candidate_ids == (2,)


def test_supplied_shared_vector_avoids_another_embedding(strategy_env):
    plan, calls = strategy_env
    vector = [.2] * 1024
    retriever.retrieve_result('q', plan=plan, strategy='dense', query_vector=vector)
    assert all(c[0] != 'embed' for c in calls)
    assert calls[0][1][0] is vector
