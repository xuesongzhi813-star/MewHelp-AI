import json

import httpx
import pytest

from app.config import get_settings
from app.kb import reranker


@pytest.fixture
def transport(monkeypatch):
    calls = []
    settings = get_settings().model_copy(update={'rerank_api_key': 'test-only-secret',
        'rerank_base_url': 'https://example.test/v1', 'rerank_timeout': 15})
    monkeypatch.setattr(reranker, 'get_settings', lambda: settings)
    def install(handler):
        def capture(request):
            calls.append(request)
            return handler(request)
        client = httpx.Client(transport=httpx.MockTransport(capture))
        monkeypatch.setattr(reranker, '_client', client)
        return calls
    yield install
    if reranker._client:
        reranker._client.close()


def test_exact_model_endpoint_top10_and_document_index_mapping(transport):
    calls = transport(lambda request: httpx.Response(200, json={'results': [
        {'index': 1, 'relevance_score': .9}, {'index': 0, 'relevance_score': .3}]}))
    assert reranker.rerank('MH-DJ101胸围', ['a', 'b']) == [(1, .9), (0, .3)]
    request = calls[0]
    assert request.method == 'POST' and str(request.url) == 'https://example.test/v1/rerank'
    body = json.loads(request.content)
    assert body == {'model': 'BAAI/bge-reranker-v2-m3', 'query': 'MH-DJ101胸围',
                    'documents': ['a', 'b'], 'top_n': 10, 'return_documents': False}
    assert request.extensions['timeout']['read'] == 15
    assert request.headers['Authorization'] == 'Bearer test-only-secret'
    assert len(calls) == 1


@pytest.mark.parametrize('results', [
    [{'index': 0, 'relevance_score': .9}, {'index': 0, 'relevance_score': .8}],
    [{'index': 2, 'relevance_score': .9}, {'index': 0, 'relevance_score': .8}],
    [{'index': -1, 'relevance_score': .9}, {'index': 0, 'relevance_score': .8}],
    [{'index': True, 'relevance_score': .9}, {'index': 0, 'relevance_score': .8}],
    [{'index': 0, 'relevance_score': 'NaN'}, {'index': 1, 'relevance_score': .8}],
    [{'index': 0, 'relevance_score': 'Infinity'}, {'index': 1, 'relevance_score': .8}],
    [{'index': 0, 'relevance_score': .9}], [], None,
])
def test_invalid_provider_result_rejected(transport, results):
    transport(lambda request: httpx.Response(200, json={'results': results}))
    with pytest.raises(reranker.RerankError) as caught:
        reranker.rerank('query', ['a', 'b'])
    assert caught.value.kind == 'invalid_response'
    assert 'test-only-secret' not in str(caught.value)


@pytest.mark.parametrize(('status', 'kind'), [(401, 'authentication'), (402, 'payment'),
    (403, 'authentication'), (429, 'rate_limit'), (500, 'provider'), (503, 'provider')])
def test_http_failure_is_classified_without_retry_or_body_leak(transport, status, kind):
    calls = transport(lambda request: httpx.Response(status, text='test-only-secret'))
    with pytest.raises(reranker.RerankError) as caught:
        reranker.rerank('query', ['a'])
    assert caught.value.kind == kind
    assert 'test-only-secret' not in str(caught.value)
    assert len(calls) == 1


def test_timeout_is_service_failure(transport):
    def timeout(request):
        raise httpx.ReadTimeout('test-only-secret', request=request)
    calls = transport(timeout)
    with pytest.raises(reranker.RerankError) as caught:
        reranker.rerank('query', ['a'])
    assert caught.value.kind == 'timeout' and len(calls) == 1
    assert 'test-only-secret' not in str(caught.value)


def test_non_json_is_invalid_response(transport):
    transport(lambda request: httpx.Response(200, text='not json'))
    with pytest.raises(reranker.RerankError, match='invalid_response'):
        reranker.rerank('query', ['a'])


def test_empty_documents_do_not_need_key_or_network(monkeypatch):
    monkeypatch.setattr(reranker, 'get_settings', lambda: pytest.fail('空文档不得读配置'))
    assert reranker.rerank('query', []) == []


def test_missing_key_fails_before_network(monkeypatch):
    settings = get_settings().model_copy(update={'rerank_api_key': '', 'embedding_api_key': ''})
    monkeypatch.setattr(reranker, 'get_settings', lambda: settings)
    with pytest.raises(reranker.RerankError) as caught:
        reranker.rerank('query', ['a'])
    assert caught.value.kind == 'configuration'


def test_key_falls_back_to_embedding_key(transport, monkeypatch):
    settings = get_settings().model_copy(update={'rerank_api_key': '', 'embedding_api_key': 'embedding-test'})
    calls = transport(lambda request: httpx.Response(200, json={'results': [{'index': 0, 'relevance_score': .7}]}))
    monkeypatch.setattr(reranker, 'get_settings', lambda: settings)
    reranker.rerank('query', ['a'])
    assert calls[0].headers['Authorization'] == 'Bearer embedding-test'
