"""建库 CLI 测试(Task 10):真实 MySQL 回滚 + Fake 向量化/Milvus。

与 ``test_kb_store.py`` / ``test_kb_dualwrite.py`` 同一套路:把
``app.kb.ingest.get_session_local`` 与 ``app.kb.store.get_session_local`` 换成
绑定外层事务连接的 sessionmaker(join_transaction_mode="create_savepoint"),
用例结束整体 rollback。用例开始前在同一事务里清空 knowledge_chunks,
保证指纹判重与全表计数只看到本用例的写入。

向量化一律用 fake:CLI 测试绝不触发真实 embedding / Milvus 破坏性调用。
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

import app.kb.ingest as ingest
import app.kb.store as store
from app.db import get_engine
from app.kb.dualwrite import Consistency, VectorizeResult
from app.models import KnowledgeChunk


class FakeMilvus:
    """只实现 status_report 需要的 ping/count,并记录调用。"""

    def __init__(self, *, ping_ok: bool = True, count_value: int = 0) -> None:
        self.ping_ok = ping_ok
        self.count_value = count_value
        self.count_calls = 0

    def ping(self) -> bool:
        return self.ping_ok

    def count(self) -> int:
        self.count_calls += 1
        return self.count_value


@pytest.fixture
def kb_tx(monkeypatch):
    """注入回滚会话工厂,并在事务内清空 chunks 保证全表扫描只看到本用例。"""
    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(ingest, "get_session_local", lambda: session_local)
        monkeypatch.setattr(store, "get_session_local", lambda: session_local)
        # 默认把向量化替换成空实现;需要断言调用顺序的用例会再次覆盖。
        monkeypatch.setattr(
            ingest, "vectorize_pending", lambda: VectorizeResult(0, 0, 0)
        )
        store.delete_all_chunks()
        try:
            yield conn
        finally:
            outer.rollback()


def _delete_chunk(conn, chunk_id: int) -> None:
    """在回滚事务内删掉一条切片,用于模拟「部分块已存在」的重跑场景。"""
    with Session(conn) as session:
        with session.begin():
            session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.id == chunk_id)
            )


# ---------------------------------------------------------------------------
# run_preview
# ---------------------------------------------------------------------------


def test_preview_does_not_write(kb_tx):
    preview = ingest.run_preview()

    assert len(preview) == 3
    assert all(item["chunks"] > 0 for item in preview)
    assert store.overview_counts()["chunks"] == 0


def test_preview_reports_source_metadata(kb_tx):
    preview = ingest.run_preview()

    for item in preview:
        assert set(item) == {
            "path",
            "title",
            "content_type",
            "chunks",
            "key_clauses",
            "features",
        }
        assert isinstance(item["path"], str)
        assert isinstance(item["features"], list)
        assert item["chunks"] >= item["key_clauses"] >= 0
        assert item["content_type"] in {"文档章节", "商品FAQ"}

    by_title = {item["title"]: item for item in preview}
    assert set(by_title) == {"退货政策", "商品FAQ", "售后手册"}
    assert by_title["商品FAQ"]["content_type"] == "商品FAQ"
    assert by_title["退货政策"]["content_type"] == "文档章节"


# ---------------------------------------------------------------------------
# ingest_docs
# ---------------------------------------------------------------------------


def test_docs_ingest_is_idempotent_by_fingerprint(kb_tx):
    first = ingest.ingest_docs()
    second = ingest.ingest_docs()

    assert first.inserted > 0
    assert first.skipped == 0
    assert len(first.chunk_ids) == first.inserted
    assert second.inserted == 0
    assert second.skipped == first.inserted
    assert second.chunk_ids == ()
    assert store.overview_counts()["chunks"] == first.inserted


def test_docs_ingest_links_siblings_per_source(kb_tx):
    result = ingest.ingest_docs()

    rows = store.fetch_by_ids(result.chunk_ids)
    assert set(rows) == set(result.chunk_ids)
    # 每份材料单独串联:3 份材料应有 3 个链头和 3 个链尾。
    assert sum(row.prev_chunk_id is None for row in rows.values()) == 3
    assert sum(row.next_chunk_id is None for row in rows.values()) == 3
    for row in rows.values():
        assert row.prev_chunk_id is None or row.prev_chunk_id in rows
        assert row.next_chunk_id is None or row.next_chunk_id in rows


def test_partial_reingest_warns_and_links_new_part_only(kb_tx, caplog):
    first = ingest.ingest_docs()
    _delete_chunk(kb_tx, first.chunk_ids[-1])

    with caplog.at_level(logging.WARNING, logger="app.kb.ingest"):
        second = ingest.ingest_docs()

    assert second.inserted == 1
    assert second.skipped == first.inserted - 1
    assert "前后块指针只覆盖新插入部分" in caplog.text

    rows = store.fetch_by_ids(second.chunk_ids)
    assert set(rows) == set(second.chunk_ids)
    # 只有一个新块,自成一个首尾都为 NULL 的链。
    assert rows[second.chunk_ids[0]].prev_chunk_id is None
    assert rows[second.chunk_ids[0]].next_chunk_id is None


def test_docs_ingest_inserts_chunk_metadata(kb_tx):
    result = ingest.ingest_docs()
    rows = store.list_recent(limit=len(result.chunk_ids))
    by_id = {row.id: row for row in rows}
    first = by_id[result.chunk_ids[0]]

    assert first.content_type in {"文档章节", "商品FAQ"}
    assert first.category
    assert first.questions
    assert first.answer
    assert first.section_path
    assert first.vectorize_status == "pending"


# ---------------------------------------------------------------------------
# ingest_faq
# ---------------------------------------------------------------------------


def test_faq_ingest_maps_category_and_content_type(kb_tx):
    result = ingest.ingest_faq()

    assert result.inserted == 13
    assert result.skipped == 0
    rows = store.list_recent(limit=20)
    assert any(
        row.content_type == "商品FAQ" and row.category == "售后政策"
        for row in rows
    )
    assert all(row.content_type == "商品FAQ" for row in rows)
    assert all(
        row.section_path == f"商品FAQ > {row.category}" for row in rows
    )
    assert all(row.is_key_clause is False for row in rows)


def test_faq_ingest_is_idempotent(kb_tx):
    first = ingest.ingest_faq()
    second = ingest.ingest_faq()

    assert first.inserted == 13
    assert second.inserted == 0
    assert second.skipped == 13
    assert store.overview_counts()["chunks"] == 13


# ---------------------------------------------------------------------------
# build_all / rebuild
# ---------------------------------------------------------------------------


def test_build_calls_docs_faq_vectorize(kb_tx, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ingest,
        "ingest_docs",
        lambda: calls.append("docs") or ingest.IngestResult(0, 0, ()),
    )
    monkeypatch.setattr(
        ingest,
        "ingest_faq",
        lambda: calls.append("faq") or ingest.IngestResult(0, 0, ()),
    )
    monkeypatch.setattr(
        ingest,
        "vectorize_pending",
        lambda: calls.append("vec") or VectorizeResult(0, 0, 0),
    )

    ingest.build_all()

    assert calls == ["docs", "faq", "vec"]


def test_build_combines_results(monkeypatch):
    monkeypatch.setattr(
        ingest, "ingest_docs", lambda: ingest.IngestResult(2, 1, (10, 11))
    )
    monkeypatch.setattr(
        ingest, "ingest_faq", lambda: ingest.IngestResult(3, 4, (12, 13, 14))
    )
    monkeypatch.setattr(ingest, "vectorize_pending", lambda: VectorizeResult(5, 0, 0))

    result = ingest.build_all()

    assert result == ingest.IngestResult(5, 5, (10, 11, 12, 13, 14))


@pytest.mark.parametrize(
    "vectorized",
    [
        VectorizeResult(processed=0, failed=2, pending_left=2),
        VectorizeResult(processed=2, failed=0, pending_left=2),
    ],
)
def test_build_raises_when_vectorize_incomplete(
    kb_tx, monkeypatch, vectorized
):
    """向量化有失败/剩余 pending 时 build 必须显式失败,且行保持 pending。"""
    monkeypatch.setattr(ingest, "vectorize_pending", lambda: vectorized)

    with pytest.raises(
        RuntimeError, match=r"向量化未完成.*补跑 vectorize 即可"
    ):
        ingest.build_all()

    counts = store.overview_counts()
    assert counts["chunks"] > 0
    assert counts["pending"] == counts["chunks"]
    assert counts["done"] == 0


def test_rebuild_delegates_to_dualwrite(monkeypatch):
    expected = VectorizeResult(9, 1, 2)
    monkeypatch.setattr(ingest.dualwrite, "rebuild_all", lambda: expected)

    assert ingest.rebuild() is expected


# ---------------------------------------------------------------------------
# status_report
# ---------------------------------------------------------------------------


def test_status_report_combines_counts_and_milvus(kb_tx, monkeypatch):
    store.insert_chunks(
        [
            store.ChunkCandidate(
                "售后", f"问{i}", f"答{i}", "路径", "文档章节", False
            )
            for i in range(2)
        ]
    )
    fake = FakeMilvus(ping_ok=True, count_value=2)
    monkeypatch.setattr(ingest.milvus, "ping", fake.ping)
    monkeypatch.setattr(ingest.milvus, "count", fake.count)
    snapshot = Consistency("consistent", (), (), "已完成精确 id 比对")
    monkeypatch.setattr(
        ingest.dualwrite, "consistency_snapshot", lambda: snapshot
    )

    report = ingest.status_report()

    assert report["chunks"] == 2
    assert report["pending"] == 2
    assert report["done"] == 0
    assert report["milvus_ok"] is True
    assert report["milvus_count"] == 2
    assert report["consistency"] == "consistent"
    assert report["consistency_note"] == "已完成精确 id 比对"
    assert fake.count_calls == 1


def test_status_report_marks_milvus_unreachable(kb_tx, monkeypatch):
    monkeypatch.setattr(ingest.milvus, "ping", lambda: False)

    def should_not_be_called():
        raise AssertionError("Milvus 不可达时不应调用 count")

    monkeypatch.setattr(ingest.milvus, "count", should_not_be_called)

    report = ingest.status_report()

    assert report["milvus_ok"] is False
    assert report["milvus_count"] is None
    assert report["consistency"] == "unknown"


# ---------------------------------------------------------------------------
# reset_all
# ---------------------------------------------------------------------------


def test_reset_requires_force(kb_tx):
    with pytest.raises(ValueError, match="--yes"):
        ingest.reset_all(force=False)


def test_reset_with_force_delegates(kb_tx, monkeypatch):
    calls = []
    monkeypatch.setattr(ingest.dualwrite, "reset_all", lambda: calls.append("reset"))

    ingest.reset_all(force=True)

    assert calls == ["reset"]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_preview_prints_result(kb_tx, capsys):
    assert ingest.main(["preview"]) == 0

    out = capsys.readouterr().out
    for title in ("退货政策", "商品FAQ", "售后手册"):
        assert title in out
    assert "块" in out
    # preview 不写库
    assert store.overview_counts()["chunks"] == 0


def test_main_docs_prints_result(kb_tx, capsys):
    assert ingest.main(["docs"]) == 0

    out = capsys.readouterr().out
    assert "新增" in out and "跳过" in out
    assert store.overview_counts()["chunks"] > 0


def test_main_faq_and_vectorize_print_result(kb_tx, capsys, monkeypatch):
    monkeypatch.setattr(
        ingest, "vectorize_pending", lambda: VectorizeResult(6, 1, 2)
    )

    assert ingest.main(["faq"]) == 0
    assert "13" in capsys.readouterr().out

    assert ingest.main(["vectorize"]) == 0
    out = capsys.readouterr().out
    assert "6" in out and "1" in out and "2" in out


def test_main_build_and_rebuild_print_result(kb_tx, capsys, monkeypatch):
    monkeypatch.setattr(
        ingest,
        "ingest_docs",
        lambda: ingest.IngestResult(1, 0, (1,)),
    )
    monkeypatch.setattr(
        ingest,
        "ingest_faq",
        lambda: ingest.IngestResult(2, 0, (2, 3)),
    )

    assert ingest.main(["build"]) == 0
    assert "3" in capsys.readouterr().out

    monkeypatch.setattr(
        ingest.dualwrite, "rebuild_all", lambda: VectorizeResult(3, 0, 0)
    )
    assert ingest.main(["rebuild"]) == 0
    assert "3" in capsys.readouterr().out


def test_main_build_exits_nonzero_when_vectorize_incomplete(
    kb_tx, capsys, monkeypatch
):
    """CLI build 必须把向量化未完成转成 stderr 报错与退出码 1。"""
    monkeypatch.setattr(
        ingest, "vectorize_pending", lambda: VectorizeResult(0, 2, 2)
    )

    assert ingest.main(["build"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "向量化未完成" in captured.err
    assert "补跑 vectorize 即可" in captured.err
    # 已入库的行保持 pending,等待补跑
    counts = store.overview_counts()
    assert counts["chunks"] > 0
    assert counts["pending"] == counts["chunks"]


def test_main_status_prints_report(kb_tx, capsys, monkeypatch):
    monkeypatch.setattr(
        ingest.milvus, "ping", lambda: True
    )
    monkeypatch.setattr(ingest.milvus, "count", lambda: 7)
    monkeypatch.setattr(
        ingest.dualwrite,
        "consistency_snapshot",
        lambda: Consistency("consistent", (), (), "已完成精确 id 比对"),
    )

    assert ingest.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "7" in out and "consistent" in out


def test_main_reset_requires_yes(kb_tx, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(ingest.dualwrite, "reset_all", lambda: calls.append("reset"))

    assert ingest.main(["reset"]) == 1
    assert calls == []
    assert "--yes" in capsys.readouterr().err

    assert ingest.main(["reset", "--yes"]) == 0
    assert calls == ["reset"]


def test_main_reports_errors_to_stderr(kb_tx, capsys, monkeypatch):
    def boom():
        raise RuntimeError("数据库打盹了")

    monkeypatch.setattr(ingest, "run_preview", boom)

    assert ingest.main(["preview"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "数据库打盹了" in captured.err
