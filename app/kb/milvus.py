"""Milvus 客户端封装。

只做连接、集合管理与向量读写,不包含任何业务语义。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import json
import math

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker
from pymilvus.milvus_client.index import IndexParams

from app.config import get_settings
from app.kb.contracts import RetrievalFilters
from app.kb.filters import compile_filter


class MilvusUnavailable(RuntimeError):
    """Milvus 客户端无法初始化或连接失败。"""


def iter_records(collection_name: str) -> Iterator[dict]:
    """遍历所有检索副本，逐页精确核对原文和元数据。"""
    iterator = get_client().query_iterator(
        collection_name=collection_name, batch_size=500, filter="",
        output_fields=["id", "text", "category", "product_category", "content_type"],
        consistency_level="Strong", timeout=30,
    )
    try:
        while True:
            try:
                batch = iterator.next()
            except StopIteration:
                break
            if not batch:
                break
            yield from batch
    finally:
        iterator.close()


# 模块级懒加载客户端;测试可 monkeypatch 成假对象。
_client: Any | None = None


def get_client() -> Any:
    """按配置懒加载 ``MilvusClient``。

    Raises:
        MilvusUnavailable: 客户端构造失败(例如地址非法或依赖缺失)。
    """
    global _client
    if _client is None:
        settings = get_settings()
        try:
            _client = MilvusClient(
                uri=settings.milvus_uri,
                token=settings.milvus_token or None,
            )
        except Exception as exc:  # noqa: BLE001 - 统一包装为可识别的连接错误
            raise MilvusUnavailable(f"Milvus 客户端初始化失败: {exc}") from exc
    return _client


def ping() -> bool:
    """探测 Milvus 是否可达;任何异常都视为不可用。"""
    try:
        get_client().list_collections()
    except Exception:  # noqa: BLE001 - ping 只关心可用性
        return False
    return True


def has_collection() -> bool:
    """返回知识集合是否已创建;连接失败时异常冒泡,由调用方判定可用性。"""
    return bool(get_client().has_collection(get_settings().knowledge_collection))


def ensure_collection() -> None:
    """确保知识集合存在且已加载;已存在时直接返回。"""
    client = get_client()
    settings = get_settings()
    collection_name = settings.knowledge_collection
    if client.has_collection(collection_name):
        return

    schema = client.create_schema(auto_id=False)
    schema.add_field(
        field_name="id",
        datatype=DataType.VARCHAR,
        is_primary=True,
        max_length=64,
    )
    schema.add_field(
        field_name="vector",
        datatype=DataType.FLOAT_VECTOR,
        dim=settings.embedding_dim,
    )
    index_params = IndexParams()
    index_params.add_index(
        field_name="vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )
    client.load_collection(collection_name)


def upsert(ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> None:
    """按主键写入或覆盖向量。

    Raises:
        ValueError: ``ids`` 与 ``vectors`` 长度不一致。
    """
    if len(ids) != len(vectors):
        raise ValueError("ids 与 vectors 长度必须一致")
    if not ids:
        return
    data = [
        {"id": str(item_id), "vector": list(vector)}
        for item_id, vector in zip(ids, vectors)
    ]
    get_client().upsert(get_settings().knowledge_collection, data)


def search(vector: Sequence[float], limit: int) -> list[tuple[str, float]]:
    """按余弦相似度检索,返回 ``(id, distance)`` 列表(保持服务端顺序)。"""
    results = get_client().search(
        get_settings().knowledge_collection,
        data=[list(vector)],
        limit=limit,
        output_fields=["id"],
        search_params={"metric_type": "COSINE"},
        anns_field='vector',
    )
    if not results:
        return []
    return [
        (str(hit["id"]), float(hit["distance"]))
        for hit in results[0]
    ]


def flush() -> None:
    """把集合的增量数据刷入持久化存储。"""
    get_client().flush(get_settings().knowledge_collection)


def drop() -> None:
    """删除知识集合。"""
    get_client().drop_collection(get_settings().knowledge_collection)


def count() -> int:
    """返回集合中的实体数量。"""
    rows = get_client().query(
        get_settings().knowledge_collection,
        filter="",
        output_fields=["count(*)"],
    )
    if not rows:
        return 0
    return int(rows[0].get("count(*)", 0))


def list_ids(max_count: int = 5000) -> list[str]:
    """列出集合中的主键,最多 ``max_count`` 条。"""
    rows = get_client().query(
        get_settings().knowledge_collection,
        filter='id != ""',
        output_fields=["id"],
        limit=max_count,
    )
    return [str(row["id"]) for row in rows]


def _hybrid_schema(client: Any):
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field('id', DataType.VARCHAR, is_primary=True, max_length=64)
    schema.add_field('vector', DataType.FLOAT_VECTOR, dim=get_settings().embedding_dim)
    schema.add_field('text', DataType.VARCHAR, max_length=65535, enable_analyzer=True,
                     analyzer_params={'type': 'chinese'})
    schema.add_field('sparse', DataType.SPARSE_FLOAT_VECTOR)
    for name, length in [('category', 255), ('product_category', 64), ('content_type', 32)]:
        schema.add_field(name, DataType.VARCHAR, max_length=length)
    schema.add_function(Function(name='text_bm25', function_type=FunctionType.BM25,
                                 input_field_names=['text'], output_field_names=['sparse']))
    return schema


def _validate_hybrid_schema(description: dict) -> None:
    fields = {f['name']: f for f in description.get('fields', [])}
    expected_types = {'id': DataType.VARCHAR, 'vector': DataType.FLOAT_VECTOR,
                      'text': DataType.VARCHAR, 'sparse': DataType.SPARSE_FLOAT_VECTOR,
                      'category': DataType.VARCHAR, 'product_category': DataType.VARCHAR,
                      'content_type': DataType.VARCHAR}
    if set(fields) != set(expected_types) or any(
        fields[n].get('type') != dtype for n, dtype in expected_types.items()
    ):
        raise ValueError('hybrid schema字段不匹配，未修改集合')
    text_params = fields['text'].get('params', {})
    analyzer = text_params.get('analyzer_params', {})
    if isinstance(analyzer, str):
        analyzer = json.loads(analyzer)
    funcs = description.get('functions', [])
    scalar_lengths_ok = all(
        int(fields[name].get('params', {}).get('max_length', 0)) == length
        for name, length in [('category', 255), ('product_category', 64), ('content_type', 32)]
    )
    bm25 = any(int(f.get('type', f.get('function_type', 0))) == int(FunctionType.BM25)
               and f.get('input_field_names') == ['text']
               and f.get('output_field_names') == ['sparse'] for f in funcs)
    if (not fields['id'].get('is_primary') or description.get('auto_id', False)
            or description.get('enable_dynamic_field', False)
            or int(fields['id'].get('params', {}).get('max_length', 0)) != 64
            or int(fields['vector'].get('params', {}).get('dim', 0)) != get_settings().embedding_dim
            or int(text_params.get('max_length', 0)) != 65535
            or str(text_params.get('enable_analyzer', '')).lower() != 'true'
            or analyzer != {'type': 'chinese'} or not bm25 or not scalar_lengths_ok):
        raise ValueError('hybrid schema配置不匹配，未修改集合')


def ensure_hybrid_collection(collection_name: str) -> None:
    client = get_client()
    if client.has_collection(collection_name):
        _validate_hybrid_schema(client.describe_collection(collection_name))
    else:
        schema = _hybrid_schema(client)
        indexes = IndexParams()
        indexes.add_index(field_name='vector', index_type='AUTOINDEX', metric_type='COSINE')
        indexes.add_index(field_name='sparse', index_type='SPARSE_INVERTED_INDEX',
                          metric_type='BM25', params={'inverted_index_algo': 'DAAT_MAXSCORE'})
        client.create_collection(collection_name=collection_name, schema=schema, index_params=indexes)
    client.load_collection(collection_name)


def upsert_records(records: Sequence[dict], *, collection_name: str) -> None:
    for record in records:
        if len(record.get('text', '').encode('utf-8')) > 65535:
            raise ValueError('text超过65535 UTF-8字节，未写入')
    if records:
        get_client().upsert(collection_name=collection_name, data=list(records), timeout=30)


def _pairs(results: list) -> list[tuple[str, float]]:
    if not results:
        return []
    pairs = [(str(hit['id']), float(hit['distance'])) for hit in results[0]]
    if any(not math.isfinite(score) for _, score in pairs):
        raise ValueError('Milvus返回非有限分数')
    return pairs


def _search_field(data: Any, field: str, metric: str, limit: int, *,
                  collection_name: str, filters: RetrievalFilters) -> list[tuple[str, float]]:
    if not 1 <= limit <= 100:
        raise ValueError('limit必须在1至100之间')
    expr, params = compile_filter(filters)
    return _pairs(get_client().search(
        collection_name=collection_name, data=[data], anns_field=field,
        search_params={'metric_type': metric}, limit=limit, output_fields=['id'],
        filter=expr, filter_params=params, timeout=10, consistency_level='Strong',
    ))


def search_dense(vector: Sequence[float], limit: int, *, collection_name: str,
                  filters: RetrievalFilters) -> list[tuple[str, float]]:
    return _search_field(list(vector), 'vector', 'COSINE', limit,
                         collection_name=collection_name, filters=filters)


def search_bm25(text: str, limit: int, *, collection_name: str,
                 filters: RetrievalFilters) -> list[tuple[str, float]]:
    return _search_field(text, 'sparse', 'BM25', limit,
                         collection_name=collection_name, filters=filters)


def search_hybrid(vector: Sequence[float], text: str, *, collection_name: str,
                   filters: RetrievalFilters) -> list[tuple[str, float]]:
    expr, params = compile_filter(filters)
    requests = [
        AnnSearchRequest(data=[data], anns_field=field, param={'metric_type': metric},
                          limit=50, expr=expr or None, expr_params=params)
        for data, field, metric in [(list(vector), 'vector', 'COSINE'), (text, 'sparse', 'BM25')]
    ]
    return _pairs(get_client().hybrid_search(
        collection_name=collection_name, reqs=requests, ranker=RRFRanker(k=60),
        limit=100, output_fields=['id'], timeout=10, consistency_level='Strong',
    ))
