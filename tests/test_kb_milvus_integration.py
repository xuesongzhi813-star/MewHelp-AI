"""Milvus 真实服务集成测试。

Milvus 未启动时自动跳过;使用独立测试集合,结束后删除。
"""

from types import SimpleNamespace

import pytest

from app.kb import milvus as milvus_mod

TEST_COLLECTION = "knowledge_test"


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
