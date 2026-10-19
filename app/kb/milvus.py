"""Milvus 客户端封装。

只做连接、集合管理与向量读写,不包含任何业务语义。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pymilvus import DataType, MilvusClient
from pymilvus.milvus_client.index import IndexParams

from app.config import get_settings


class MilvusUnavailable(RuntimeError):
    """Milvus 客户端无法初始化或连接失败。"""


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
