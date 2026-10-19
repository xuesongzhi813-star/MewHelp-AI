"""双写状态机测试(Task 8):真实 MySQL 回滚 + Fake embedding/Milvus。

与 ``test_kb_store.py`` 同一套路:把 ``app.kb.store.get_session_local`` 换成
绑定外层事务连接的 sessionmaker(join_transaction_mode="create_savepoint")。
用例开始前在同一事务里清空 knowledge_chunks,使 ``vectorize_pending`` 的
全表扫描只看到本用例的行;用例结束整体 rollback,已提交数据原样恢复。

Fake 的语义对齐 ``app.kb.milvus`` / ``app.kb.embedding`` 的公开行为:
- embedding 失败抛 :class:`EmbeddingError`;
- Milvus 不可用抛 :class:`MilvusUnavailable`;
- ``upsert`` 按主键覆盖,``drop`` 清空集合。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

import app.kb.dualwrite as dualwrite
import app.kb.store as store
from app.db import get_engine
from app.kb.documents import embedding_text
from app.kb.embedding import EmbeddingError
from app.kb.milvus import MilvusUnavailable


class FakeMilvus:
    """记录调用并模拟按主键覆盖的 Milvus 集合。"""

    def __init__(
        self,
        *,
        ids=(),
        ping_ok: bool = True,
        raise_on_ping: bool = False,
        collection_present: bool = True,
        raise_on_has_collection: bool = False,
        raise_on_list_ids: bool = False,
        raise_on_upsert_calls=(),
        raise_on_flush: bool = False,
        raise_on_flush_calls=(),
        raise_on_count: bool = False,
        raise_on_ensure: bool = False,
        count_value: int | None = None,
    ) -> None:
        self.ids: set[str] = {str(item) for item in ids}
        self.ping_ok = ping_ok
        self.raise_on_ping = raise_on_ping
        self.collection_present = collection_present
        self.raise_on_has_collection = raise_on_has_collection
        self.raise_on_list_ids = raise_on_list_ids
        self.raise_on_upsert_calls = set(raise_on_upsert_calls)
        self.raise_on_flush = raise_on_flush
        self.raise_on_flush_calls = set(raise_on_flush_calls)
        self.raise_on_count = raise_on_count
        self.raise_on_ensure = raise_on_ensure
        self.count_value = count_value
        self.upserted: list[dict] = []
        self.upsert_batches: list[list[dict]] = []
        self.upsert_calls = 0
        self.flush_calls = 0
        self.drop_calls = 0
        self.ensure_calls = 0
        self.has_collection_calls = 0
        self.list_ids_calls = 0
        self.count_calls = 0

    def ping(self) -> bool:
        if self.raise_on_ping:
            raise MilvusUnavailable("milvus down")
        return self.ping_ok

    def has_collection(self) -> bool:
        self.has_collection_calls += 1
        if self.raise_on_has_collection:
            raise MilvusUnavailable("has_collection failed")
        return self.collection_present

    def ensure_collection(self) -> None:
        self.ensure_calls += 1
        if self.raise_on_ensure:
            raise MilvusUnavailable("ensure failed")

    def upsert(self, ids, vectors) -> None:
        call_index = self.upsert_calls
        self.upsert_calls += 1
        if call_index in self.raise_on_upsert_calls:
            raise MilvusUnavailable("upsert failed")
        rows = [
            {"id": str(item_id), "vector": list(vector)}
            for item_id, vector in zip(ids, vectors)
        ]
        self.upsert_batches.append(rows)
        self.upserted.extend(rows)
        self.ids.update(row["id"] for row in rows)

    def flush(self) -> None:
        call_index = self.flush_calls
        self.flush_calls += 1
        if self.raise_on_flush or call_index in self.raise_on_flush_calls:
            raise MilvusUnavailable("flush failed")

    def drop(self) -> None:
        self.drop_calls += 1
        self.ids.clear()

    def count(self) -> int:
        self.count_calls += 1
        if self.raise_on_count:
            raise MilvusUnavailable("count failed")
        return self.count_value if self.count_value is not None else len(self.ids)

    def list_ids(self) -> list[str]:
        self.list_ids_calls += 1
        if self.raise_on_list_ids:
            raise MilvusUnavailable("list_ids failed")
        return sorted(self.ids)


@pytest.fixture
def kb_tx(monkeypatch):
    """注入回滚会话工厂,并在事务内清空 chunks 保证全表扫描只看到本用例。"""
    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(store, "get_session_local", lambda: session_local)
        # 事务内清空;rollback 后其他已提交行原样恢复。
        store.delete_all_chunks()
        try:
            yield conn
        finally:
            outer.rollback()


def _candidate(questions="问", answer="答", **overrides) -> store.ChunkCandidate:
    values = {
        "category": "售后",
        "questions": questions,
        "answer": answer,
        "section_path": "售后 > 节1",
        "content_type": "文档章节",
        "is_key_clause": False,
    }
    values.update(overrides)
    return store.ChunkCandidate(**values)


def _install_embed(monkeypatch, *, fail_calls=()) -> list[list[str]]:
    """替换 dualwrite.embed_texts,记录每批文本;指定调用序号抛 EmbeddingError。"""
    calls: list[list[str]] = []
    fail_calls = set(fail_calls)

    def fake_embed(texts):
        index = len(calls)
        calls.append(list(texts))
        if index in fail_calls:
            raise EmbeddingError("boom")
        return [[0.1] * 1024 for _ in texts]

    monkeypatch.setattr(dualwrite, "embed_texts", fake_embed)
    return calls


def _install_settings(monkeypatch, batch_size: int) -> None:
    class Settings:
        embedding_batch_size = batch_size

    monkeypatch.setattr(dualwrite, "get_settings", lambda: Settings())


# ---------------------------------------------------------------------------
# vectorize_pending
# ---------------------------------------------------------------------------


def test_pending_becomes_done_and_upserts_str_id(kb_tx, monkeypatch):
    ids = store.insert_chunks([_candidate("问", "答")])
    fake_milvus = FakeMilvus()
    calls = _install_embed(monkeypatch)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    result = dualwrite.vectorize_pending()

    assert result == dualwrite.VectorizeResult(processed=1, failed=0, pending_left=0)
    # 全新 Milvus 上集合可能还不存在:向量化必须先幂等建集合。
    assert fake_milvus.ensure_calls == 1
    assert fake_milvus.upserted[0]["id"] == str(ids[0])
    assert all(isinstance(row["id"], str) for row in fake_milvus.upserted)
    assert fake_milvus.flush_calls == 1
    # 送去嵌入的是统一的「分类/问题/内容」模板
    assert calls == [[embedding_text("售后", "问", "答")]]

    row = store.fetch_by_ids(ids)[ids[0]]
    assert row.vectorize_status == "done"
    assert row.vector_id == str(ids[0])


def test_ensure_collection_failure_propagates(kb_tx, monkeypatch):
    """Milvus 建集合失败时向调用方冒泡,而不是吞成 failed 后假装还能补跑。"""
    store.insert_chunks([_candidate("问", "答")])
    fake_milvus = FakeMilvus(raise_on_ensure=True)
    calls = _install_embed(monkeypatch)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    with pytest.raises(MilvusUnavailable):
        dualwrite.vectorize_pending()

    assert calls == []
    assert fake_milvus.upserted == []


def test_embedding_failure_leaves_pending(kb_tx, monkeypatch):
    ids = store.insert_chunks([_candidate("问", "答")])
    fake_milvus = FakeMilvus()
    _install_embed(monkeypatch, fail_calls={0})
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    result = dualwrite.vectorize_pending()

    assert result.processed == 0
    assert result.failed == 1
    assert result.pending_left == 1
    assert fake_milvus.upserted == []
    assert fake_milvus.flush_calls == 0
    assert store.fetch_by_ids(ids)[ids[0]].vectorize_status == "pending"


def test_rerun_after_failure_converges(kb_tx, monkeypatch):
    ids = store.insert_chunks([_candidate("问", "答")])
    fake_milvus = FakeMilvus()
    calls = _install_embed(monkeypatch, fail_calls={0})
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    first = dualwrite.vectorize_pending()
    second = dualwrite.vectorize_pending()

    assert (first.processed, first.failed, first.pending_left) == (0, 1, 1)
    assert (second.processed, second.failed, second.pending_left) == (1, 0, 0)
    assert len(calls) == 2  # 失败批次在第二次被重新取到
    assert fake_milvus.ids == {str(ids[0])}
    assert len(fake_milvus.upserted) == 1
    assert store.fetch_by_ids(ids)[ids[0]].vectorize_status == "done"


def test_failure_continues_to_next_batch(kb_tx, monkeypatch):
    """单批失败只记该批 failed,后续批次继续处理且不会死循环。"""
    ids = store.insert_chunks([_candidate("问1", "答1"), _candidate("问2", "答2")])
    fake_milvus = FakeMilvus()
    calls = _install_embed(monkeypatch, fail_calls={0})
    _install_settings(monkeypatch, batch_size=1)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    result = dualwrite.vectorize_pending()

    assert result.processed == 1
    assert result.failed == 1
    assert result.pending_left == 1
    assert len(calls) == 2
    assert fake_milvus.ids == {str(ids[1])}
    rows = store.fetch_by_ids(ids)
    assert rows[ids[0]].vectorize_status == "pending"
    assert rows[ids[1]].vectorize_status == "done"


def test_failed_counts_every_row_in_batch(kb_tx, monkeypatch):
    """失败计数按整批行数累计(2 行批次失败必须记 failed=2,而非 1)。"""
    ids = store.insert_chunks(
        [_candidate("问1", "答1"), _candidate("问2", "答2"), _candidate("问3", "答3")]
    )
    fake_milvus = FakeMilvus()
    calls = _install_embed(monkeypatch, fail_calls={0})
    _install_settings(monkeypatch, batch_size=2)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    result = dualwrite.vectorize_pending()

    assert result.processed == 1
    assert result.failed == 2
    assert result.pending_left == 2  # 失败批次的两行仍 pending
    assert [len(batch) for batch in calls] == [2, 1]
    rows = store.fetch_by_ids(ids)
    assert [rows[item].vectorize_status for item in ids] == [
        "pending",
        "pending",
        "done",
    ]


def test_flush_failure_leaves_pending_and_rerun_converges(kb_tx, monkeypatch):
    """flush 失败时行必须保持 pending;重跑用幂等 upsert 收敛。"""
    ids = store.insert_chunks([_candidate("问1", "答1"), _candidate("问2", "答2")])
    fake_milvus = FakeMilvus(raise_on_flush_calls={0})
    _install_embed(monkeypatch)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    first = dualwrite.vectorize_pending()

    assert first.processed == 0
    assert first.failed == len(ids)
    assert first.pending_left == len(ids)
    rows = store.fetch_by_ids(ids)
    assert all(row.vectorize_status == "pending" for row in rows.values())
    assert all(row.vector_id is None for row in rows.values())

    second = dualwrite.vectorize_pending()

    assert second == dualwrite.VectorizeResult(
        processed=len(ids), failed=0, pending_left=0
    )
    assert fake_milvus.ids == {str(item) for item in ids}
    rows = store.fetch_by_ids(ids)
    assert all(row.vectorize_status == "done" for row in rows.values())


def test_upsert_failure_leaves_pending_and_rerun_converges(kb_tx, monkeypatch):
    ids = store.insert_chunks([_candidate("问", "答")])
    fake_milvus = FakeMilvus(raise_on_upsert_calls={0})
    _install_embed(monkeypatch)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    first = dualwrite.vectorize_pending()

    assert (first.processed, first.failed, first.pending_left) == (0, 1, 1)
    assert store.fetch_by_ids(ids)[ids[0]].vectorize_status == "pending"

    second = dualwrite.vectorize_pending()

    assert (second.processed, second.failed, second.pending_left) == (1, 0, 0)
    assert fake_milvus.ids == {str(ids[0])}
    assert store.fetch_by_ids(ids)[ids[0]].vectorize_status == "done"


def test_batching_uses_embedding_batch_size(kb_tx, monkeypatch):
    store.insert_chunks([_candidate(f"问{i}", f"答{i}") for i in range(3)])
    fake_milvus = FakeMilvus()
    calls = _install_embed(monkeypatch)
    _install_settings(monkeypatch, batch_size=2)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    result = dualwrite.vectorize_pending()

    assert result == dualwrite.VectorizeResult(processed=3, failed=0, pending_left=0)
    assert [len(batch) for batch in calls] == [2, 1]
    assert fake_milvus.flush_calls == 2


def test_limit_caps_processed_rows(kb_tx, monkeypatch):
    store.insert_chunks([_candidate(f"问{i}", f"答{i}") for i in range(3)])
    fake_milvus = FakeMilvus()
    calls = _install_embed(monkeypatch)
    _install_settings(monkeypatch, batch_size=2)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    result = dualwrite.vectorize_pending(limit=1)

    assert result == dualwrite.VectorizeResult(processed=1, failed=0, pending_left=2)
    assert [len(batch) for batch in calls] == [1]


def test_idempotent_upsert_on_rerun(kb_tx, monkeypatch):
    """同一 id 再次写入仍是单条向量(按主键覆盖),MySQL 不会多出行。"""
    ids = store.insert_chunks([_candidate("问", "答")])
    fake_milvus = FakeMilvus()
    _install_embed(monkeypatch)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    dualwrite.vectorize_pending()
    store.reset_all_pending()
    second = dualwrite.vectorize_pending()

    assert second.processed == 1 and second.pending_left == 0
    assert [row["id"] for row in fake_milvus.upserted] == [str(ids[0]), str(ids[0])]
    assert fake_milvus.ids == {str(ids[0])}
    assert len(store.done_ids()) == 1
    assert store.overview_counts()["chunks"] == 1


def test_empty_pending_returns_zero(kb_tx, monkeypatch):
    fake_milvus = FakeMilvus()
    calls = _install_embed(monkeypatch)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    result = dualwrite.vectorize_pending()

    assert result == dualwrite.VectorizeResult(processed=0, failed=0, pending_left=0)
    assert calls == []
    assert fake_milvus.upserted == []
    assert fake_milvus.flush_calls == 0


# ---------------------------------------------------------------------------
# consistency_snapshot
# ---------------------------------------------------------------------------


def test_consistency_consistent(kb_tx, monkeypatch):
    ids = store.insert_chunks([_candidate("问1", "答1"), _candidate("问2", "答2")])
    store.mark_done(ids)
    fake_milvus = FakeMilvus(ids=[str(item) for item in ids])
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "consistent"
    assert snapshot.missing_in_milvus == ()
    assert snapshot.orphan_in_milvus == ()
    assert "精确" in snapshot.note


def test_consistency_mismatch_lists_missing_and_orphan(kb_tx, monkeypatch):
    ids = store.insert_chunks(
        [_candidate("问1", "答1"), _candidate("问2", "答2"), _candidate("问3", "答3")]
    )
    store.mark_done(ids)
    fake_milvus = FakeMilvus(ids=[str(ids[0]), str(ids[2]), "999999"])
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "mismatch"
    assert snapshot.missing_in_milvus == (str(ids[1]),)
    assert snapshot.orphan_in_milvus == ("999999",)
    assert "精确" in snapshot.note


def test_consistency_large_library_uses_count_only(kb_tx, monkeypatch):
    """超过 5000 行时不再调用 list_ids,避免「没列全」被误报成 missing。"""
    done = [str(index) for index in range(6000)]
    monkeypatch.setattr(dualwrite.store, "done_ids", lambda: done)
    fake_milvus = FakeMilvus(count_value=len(done))
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "consistent"
    assert snapshot.missing_in_milvus == ()
    assert snapshot.orphan_in_milvus == ()
    assert snapshot.note == "仅数量比对(超过 5000 行)"
    assert fake_milvus.list_ids_calls == 0


def test_consistency_large_library_count_mismatch(kb_tx, monkeypatch):
    done = [str(index) for index in range(6000)]
    monkeypatch.setattr(dualwrite.store, "done_ids", lambda: done)
    fake_milvus = FakeMilvus(count_value=5999)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "mismatch"
    assert snapshot.missing_in_milvus == ()
    assert snapshot.orphan_in_milvus == ()
    assert snapshot.note == "仅数量比对(超过 5000 行)"
    assert fake_milvus.list_ids_calls == 0


def test_consistency_unknown_when_milvus_down(kb_tx, monkeypatch):
    fake_milvus = FakeMilvus(raise_on_ping=True)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot == dualwrite.Consistency(
        status="unknown",
        missing_in_milvus=(),
        orphan_in_milvus=(),
        note="Milvus 读不到",
    )


def test_consistency_absent_collection_with_empty_mysql_is_consistent(
    kb_tx, monkeypatch
):
    """服务可达但集合未创建且 MySQL 无 done 行:一致,不是 unknown。"""
    fake_milvus = FakeMilvus(collection_present=False)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot == dualwrite.Consistency(
        status="consistent",
        missing_in_milvus=(),
        orphan_in_milvus=(),
        note="集合未创建",
    )
    assert fake_milvus.has_collection_calls == 1
    assert fake_milvus.count_calls == 0
    assert fake_milvus.list_ids_calls == 0


def test_consistency_absent_collection_with_done_rows_is_mismatch(
    kb_tx, monkeypatch
):
    """集合未创建但 MySQL 有 done 行:这些向量确实缺失,必须报 mismatch。"""
    ids = store.insert_chunks([_candidate("问1", "答1"), _candidate("问2", "答2")])
    store.mark_done(ids)
    fake_milvus = FakeMilvus(collection_present=False)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "mismatch"
    assert snapshot.missing_in_milvus == tuple(sorted(str(item) for item in ids))
    assert snapshot.orphan_in_milvus == ()
    assert snapshot.note == "集合未创建"
    assert fake_milvus.count_calls == 0


def test_consistency_unknown_when_has_collection_fails(kb_tx, monkeypatch):
    """读不到集合状态仍按「读不到」处理,绝不能当成集合不存在。"""
    fake_milvus = FakeMilvus(raise_on_has_collection=True)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "unknown"
    assert snapshot.note == "Milvus 读不到"


def test_consistency_unknown_when_mysql_fails(kb_tx, monkeypatch):
    fake_milvus = FakeMilvus()
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    def broken_done_ids():
        raise RuntimeError("mysql down")

    monkeypatch.setattr(dualwrite.store, "done_ids", broken_done_ids)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "unknown"
    assert snapshot.missing_in_milvus == ()
    assert snapshot.orphan_in_milvus == ()


def test_consistency_unknown_when_list_ids_fails(kb_tx, monkeypatch):
    """Milvus 读取异常必须报 unknown,绝不能当成「没有匹配」。"""
    fake_milvus = FakeMilvus(raise_on_list_ids=True)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "unknown"
    assert snapshot.missing_in_milvus == ()
    assert snapshot.orphan_in_milvus == ()


def test_consistency_unknown_when_count_fails(kb_tx, monkeypatch):
    fake_milvus = FakeMilvus(raise_on_count=True)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    snapshot = dualwrite.consistency_snapshot()

    assert snapshot.status == "unknown"
    assert snapshot.note == "Milvus 读不到"


# ---------------------------------------------------------------------------
# rebuild_all / reset_all
# ---------------------------------------------------------------------------


def test_rebuild_all_resets_and_revectorizes(kb_tx, monkeypatch):
    ids = store.insert_chunks([_candidate("问1", "答1"), _candidate("问2", "答2")])
    fake_milvus = FakeMilvus()
    _install_embed(monkeypatch)
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)
    dualwrite.vectorize_pending()
    fake_milvus.ids.add("orphan")  # 重建后应被 drop 清掉

    result = dualwrite.rebuild_all()

    assert result == dualwrite.VectorizeResult(processed=2, failed=0, pending_left=0)
    assert fake_milvus.drop_calls == 1
    # 初次 vectorize + rebuild 显式 ensure + rebuild 内 vectorize,共 3 次。
    assert fake_milvus.ensure_calls == 3
    assert fake_milvus.ids == {str(item) for item in ids}
    assert set(store.done_ids()) == {str(item) for item in ids}
    assert store.overview_counts()["pending"] == 0


def test_reset_all_clears_mysql_and_milvus(kb_tx, monkeypatch):
    ids = store.insert_chunks([_candidate("问", "答")])
    store.mark_done(ids)
    fake_milvus = FakeMilvus(ids=[str(ids[0])])
    monkeypatch.setattr(dualwrite, "milvus", fake_milvus)

    dualwrite.reset_all()

    assert store.overview_counts()["chunks"] == 0
    assert fake_milvus.drop_calls == 1
    assert fake_milvus.ids == set()
