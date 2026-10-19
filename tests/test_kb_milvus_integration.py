"""Milvus 真实服务集成测试。

Milvus 未启动时自动跳过;使用独立测试集合,结束后删除。
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import get_engine
from app.kb import dualwrite
from app.kb import milvus as milvus_mod
from app.kb import store

TEST_COLLECTION = "knowledge_test"
FRESH_COLLECTION = "knowledge_fresh_test"


@pytest.mark.integration
def test_milvus_roundtrip(monkeypatch):
    if not milvus_mod.ping():
        pytest.skip("Milvus 未启动")

    settings = milvus_mod.get_settings()
    test_settings = SimpleNamespace(
        milvus_uri=settings.milvus_uri,
        milvus_token=settings.milvus_token,
        knowledge_collection=TEST_COLLECTION,
        embedding_dim=settings.embedding_dim,
    )
    monkeypatch.setattr(milvus_mod, "get_settings", lambda: test_settings)
    monkeypatch.setattr(milvus_mod, "_client", None)

    client = milvus_mod.get_client()
    try:
        if client.has_collection(TEST_COLLECTION):
            milvus_mod.drop()

        milvus_mod.ensure_collection()

        dim = settings.embedding_dim
        vector_a = [1.0] + [0.0] * (dim - 1)
        vector_b = [0.0, 1.0] + [0.0] * (dim - 2)
        milvus_mod.upsert(["a", "b"], [vector_a, vector_b])
        milvus_mod.flush()

        results = milvus_mod.search(vector_a, limit=2)

        assert [item_id for item_id, _ in results] == ["a", "b"]
        assert results[0][1] == pytest.approx(1.0, abs=1e-5)
        assert results[0][1] > results[1][1]
        assert milvus_mod.count() == 2
        assert set(milvus_mod.list_ids()) == {"a", "b"}
    finally:
        if client.has_collection(TEST_COLLECTION):
            milvus_mod.drop()


@pytest.mark.integration
def test_vectorize_pending_creates_missing_collection(monkeypatch):
    """全新 Milvus 上从「集合不存在」开始,vectorize_pending 必须自动建集合。

    故意使用独立的测试集合,绝不删除真实的 ``knowledge`` 集合。
    """
    if not milvus_mod.ping():
        pytest.skip("Milvus 未启动")

    real_settings = milvus_mod.get_settings()
    test_settings = SimpleNamespace(
        milvus_uri=real_settings.milvus_uri,
        milvus_token=real_settings.milvus_token,
        knowledge_collection=FRESH_COLLECTION,
        embedding_dim=real_settings.embedding_dim,
    )
    monkeypatch.setattr(milvus_mod, "get_settings", lambda: test_settings)
    monkeypatch.setattr(milvus_mod, "_client", None)

    client = milvus_mod.get_client()
    # 从一个确实不存在的集合开始;只清理由本用例命名的集合。
    if client.has_collection(FRESH_COLLECTION):
        client.drop_collection(FRESH_COLLECTION)
    assert client.has_collection(FRESH_COLLECTION) is False

    # 嵌入用 fake,避免依赖外部 API;向量维度必须与集合一致。
    monkeypatch.setattr(
        dualwrite,
        "embed_texts",
        lambda texts: [
            [0.001] * real_settings.embedding_dim for _ in texts
        ],
    )

    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(store, "get_session_local", lambda: session_local)
        try:
            ids = store.insert_chunks(
                [
                    store.ChunkCandidate(
                        "售后",
                        f"全新集合问题 {index}?",
                        f"全新集合答案 {index}。",
                        "售后 > 测试",
                        "文档章节",
                        False,
                    )
                    for index in range(2)
                ]
            )

            # 真实库里可能已有验收提交的 done 行;本用例只比较自己插入的
            # 2 行与独立测试集合,避免把全局数据算进空集合的比对。
            monkeypatch.setattr(
                store, "done_ids", lambda: [str(item) for item in ids]
            )

            result = dualwrite.vectorize_pending()

            assert result.processed == 2
            assert result.failed == 0
            assert result.pending_left == 0
            # 关键断言:调用前集合不存在,调用后自动创建。
            assert client.has_collection(FRESH_COLLECTION) is True
            rows = store.fetch_by_ids(ids)
            assert all(
                rows[item].vectorize_status == "done" for item in ids
            )
            assert {rows[item].vector_id for item in ids} == {
                str(item) for item in ids
            }
            assert milvus_mod.count() == 2

            snapshot = dualwrite.consistency_snapshot()
            assert snapshot.status == "consistent"
            assert snapshot.missing_in_milvus == ()
            assert snapshot.orphan_in_milvus == ()
        finally:
            outer.rollback()  # MySQL 侧的 pending/done 行全部回滚
            if client.has_collection(FRESH_COLLECTION):
                client.drop_collection(FRESH_COLLECTION)
