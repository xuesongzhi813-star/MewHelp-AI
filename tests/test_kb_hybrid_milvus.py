from copy import deepcopy
import json
import pytest
from pymilvus import DataType, FunctionType, MilvusClient

from app.kb import milvus
from app.kb.contracts import RetrievalFilters
from app.kb.filters import compile_filter


class Client:
    def __init__(self):
        self.created = None
        self.calls = []
        self.description = None

    def has_collection(self, name):
        return self.description is not None

    def create_schema(self, **kwargs):
        return MilvusClient.create_schema(**kwargs)

    def create_collection(self, **kwargs):
        self.created = kwargs
        self.description = kwargs['schema'].to_dict()

    def describe_collection(self, name):
        return deepcopy(self.description)

    def load_collection(self, name):
        self.calls.append(('load', name))

    def hybrid_search(self, **kwargs):
        self.calls.append(('hybrid', kwargs))
        return [[{'id': '5', 'distance': .03}]]

    def search(self, **kwargs):
        self.calls.append(('search', kwargs))
        return [[{'id': '8', 'distance': 3.2}]]

    def upsert(self, **kwargs):
        self.calls.append(('upsert', kwargs))


@pytest.fixture
def client(monkeypatch):
    fake = Client()
    monkeypatch.setattr(milvus, 'get_client', lambda: fake)
    return fake


def test_schema_has_native_chinese_bm25_function_and_indexes(client):
    milvus.ensure_hybrid_collection('ch04_unit')
    schema = client.created['schema']
    fields = {f.name: f for f in schema.fields}
    assert set(fields) == {'id', 'vector', 'text', 'sparse', 'category', 'product_category', 'content_type'}
    assert fields['id'].is_primary and fields['id'].params['max_length'] == 64
    assert fields['vector'].params['dim'] == 1024
    assert fields['sparse'].dtype == DataType.SPARSE_FLOAT_VECTOR
    assert fields['text'].params['max_length'] == 65535
    assert fields['text'].params['enable_analyzer'] is True
    assert json.loads(fields['text'].params['analyzer_params']) == {'type': 'chinese'}
    fn = schema.functions[0].to_dict()
    assert fn['type'] == FunctionType.BM25
    assert fn['input_field_names'] == ['text'] and fn['output_field_names'] == ['sparse']
    indexes = [p.to_dict() for p in client.created['index_params']]
    assert any(p['field_name'] == 'sparse' and p['metric_type'] == 'BM25' for p in indexes)


def test_existing_incompatible_schema_is_rejected(client):
    milvus.ensure_hybrid_collection('ch04_unit')
    client.description['fields'] = [f for f in client.description['fields'] if f['name'] != 'text']
    with pytest.raises(ValueError, match='schema'):
        milvus.ensure_hybrid_collection('ch04_unit')


def test_existing_too_short_metadata_field_is_rejected(client):
    milvus.ensure_hybrid_collection('ch04_unit')
    field = next(f for f in client.description['fields'] if f['name'] == 'category')
    field['params']['max_length'] = 1
    with pytest.raises(ValueError, match='schema'):
        milvus.ensure_hybrid_collection('ch04_unit')


def test_hybrid_uses_two_filtered_top50_requests_and_rrf(client):
    filters = RetrievalFilters(product_category='衬衫', include_general=True)
    result = milvus.search_hybrid([.1] * 1024, 'MH-SH202材质', collection_name='ch04_unit', filters=filters)
    kwargs = client.calls[-1][1]
    dense, sparse = kwargs['reqs']
    assert dense.limit == sparse.limit == 50
    assert kwargs['limit'] == 100
    assert kwargs['ranker'].dict() == {'strategy': 'rrf', 'params': {'k': 60}}
    assert dense.anns_field == 'vector' and sparse.anns_field == 'sparse'
    assert dense.expr_params == sparse.expr_params == {'product_category': ['衬衫', '通用']}
    assert dense.expr == sparse.expr == 'product_category in {product_category}'
    assert result == [('5', .03)]


def test_filter_values_are_bound_not_interpolated():
    malicious = '" or id != "'
    expr, params = compile_filter(RetrievalFilters(category=malicious))
    assert expr == 'category == {category}'
    assert params == {'category': malicious}
    assert malicious not in expr


def test_bm25_sends_text_and_filter_without_embedding(client):
    assert milvus.search_bm25('MH-DJ101胸围', 50, collection_name='ch04_unit',
                              filters=RetrievalFilters(product_category='牛仔夹克')) == [('8', 3.2)]
    call = client.calls[-1][1]
    assert call['data'] == ['MH-DJ101胸围'] and call['anns_field'] == 'sparse'
    assert call['filter_params'] == {'product_category': ['牛仔夹克']}


def test_utf8_limit_rejected_before_any_network_write(client):
    with pytest.raises(ValueError, match='65535'):
        milvus.upsert_records([{'id': '1', 'text': '衣' * 21846}], collection_name='ch04_unit')
    assert client.calls == []
