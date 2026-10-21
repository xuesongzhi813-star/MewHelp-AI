from types import SimpleNamespace

import pytest

import app.api.kb as api
from app.config import get_settings
from app.kb.contracts import Hit, QueryPlan, RetrievalResult
from tests.test_kb_retriever import _stored


@pytest.fixture
def ch04_api(monkeypatch):
    settings = get_settings().model_copy(update={'kb_corpus_version': 'ch04'})
    monkeypatch.setattr(api, 'get_settings', lambda: settings)
    monkeypatch.setattr(api, 'search_hits', lambda *a: pytest.fail('ch04不可调用旧检索'))
    monkeypatch.setattr(api.milvus, 'ping', lambda: True)
    monkeypatch.setattr(api.milvus, 'get_client', lambda: SimpleNamespace(has_collection=lambda n: False))
    return settings


@pytest.mark.parametrize('overrides', [
    {'route': 'unsupported'}, {'top_k': 0}, {'top_k': 51},
    {'filters': {'product_category': '猫粮'}}, {'filters': {'expr': 'id != ""'}},
])
async def test_invalid_search_parameters_are_422(client, ch04_api, overrides):
    response = await client.post('/api/kb/search', json={'query': '规格', **overrides})
    assert response.status_code == 422


@pytest.mark.parametrize('route', ['dense', 'bm25', 'hybrid', 'hybrid_rerank'])
async def test_search_preserves_score_types_and_only_crops_display(client, ch04_api, monkeypatch, route):
    captured = []
    def retrieve(query, **kwargs):
        captured.append((query, kwargs))
        plan = QueryPlan(raw_query=query, normalized_query=query, lexical_query=query, filters=kwargs['filters'])
        hits = tuple(Hit(n, .02, '商品', '问', '原文', '文档章节', '章', score_type='fusion') for n in range(1, 13))
        return RetrievalResult(plan, route, hits, tuple(range(1, 13)))
    monkeypatch.setattr(api, 'retrieve_result', retrieve, raising=False)
    response = await client.post('/api/kb/search', json={'query': 'MH-SH202材质', 'route': route,
                        'top_k': 2, 'filters': {'product_category': '衬衫'}})
    assert response.status_code == 200
    data = response.json()
    assert len(data['hits']) == 2 and data['hits'][0]['score_type'] == 'fusion'
    assert data['candidate_ids'] == list(range(1, 13))
    assert data['strategy'] == route and data['message'] is None
    assert captured[0][1]['strategy'] == route
    assert captured[0][1]['filters'].product_category == '衬衫'


async def test_search_service_error_is_502_and_does_not_echo_private_message(client, ch04_api, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError('private provider token')
    monkeypatch.setattr(api, 'retrieve_result', broken, raising=False)
    response = await client.post('/api/kb/search', json={'query': '规格', 'route': 'hybrid_rerank'})
    assert response.status_code == 502
    assert 'private' not in response.text and '无匹配' not in response.text


async def test_chunk_original_is_mysql_text_and_server_link(client, monkeypatch):
    row = _stored(42, questions='型号问题', answer='<script>alert(1)</script>\n原文', section_path='古着 > MH-DJ101')
    monkeypatch.setattr(api.store, 'fetch_by_ids', lambda ids: {42: row})
    response = await client.get('/api/kb/chunks/42')
    assert response.status_code == 200
    data = response.json()
    assert data['id'] == 42 and data['answer'] == row.answer
    assert data['section_path'] == row.section_path and data['original_url'] == '/kb?chunk_id=42'
    assert response.headers['content-type'].startswith('application/json')


async def test_chunk_missing_and_service_failure_are_distinct(client, monkeypatch):
    monkeypatch.setattr(api.store, 'fetch_by_ids', lambda ids: {})
    assert (await client.get('/api/kb/chunks/987654')).status_code == 404
    def fail(ids):
        raise RuntimeError('private database details')
    monkeypatch.setattr(api.store, 'fetch_by_ids', fail)
    response = await client.get('/api/kb/chunks/42')
    assert response.status_code == 503 and 'private' not in response.text
    assert (await client.get('/api/kb/chunks/-1')).status_code == 422


async def test_overview_uses_current_corpus_counts_not_all_done(client, ch04_api, monkeypatch):
    from app.kb import migration
    monkeypatch.setattr(api.store, 'overview_counts', lambda: {'chunks': 100, 'pending': 0, 'done': 100, 'key_clauses': 1})
    monkeypatch.setattr(api.store, 'staging_counts', lambda: {})
    monkeypatch.setattr(api.store, 'list_recent', lambda n: [])
    monkeypatch.setattr(api.milvus, 'has_collection', lambda: pytest.fail('不能读旧集合状态'))
    monkeypatch.setattr(migration, 'verify_hybrid', lambda: {
        'status': 'consistent', 'mysql_total': 100, 'eligible_total': 65, 'legacy_excluded': 35,
        'eligible_pending': 0, 'eligible_done': 65, 'target_count': 65,
        'missing_in_milvus': [], 'orphan_in_milvus': [], 'stale_ids': []})
    data = (await client.get('/api/kb/overview')).json()
    assert data['mysql']['mysql_total'] == 100 and data['mysql']['eligible_total'] == 65
    assert data['milvus']['collection'] == 'knowledge_hybrid' and data['milvus']['count'] == 65
    assert data['consistency']['status'] == 'consistent'


async def test_not_validated_migration_returns_explicit_unavailable(client, ch04_api, monkeypatch):
    from app.kb import migration
    monkeypatch.setattr(migration, 'load_manifest', lambda: SimpleNamespace(phase='indexing'))
    response = await client.post('/api/kb/search', json={'query': '规格', 'route': 'hybrid'})
    assert response.status_code == 502
    assert '迁移' in response.json()['detail']
