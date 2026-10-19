"""在线检索测试(Task 9):Fake embedding / Milvus / MySQL,不连真实服务。

检索链路 = embed_texts(query) → milvus.search → store.fetch_by_ids,
本文件只验证编排语义:
- 空白 query 短路,不触发任何外部调用;
- 分数阈值过滤与命中顺序保持;
- 默认参数取自 Settings;
- Hit 字段映射、MySQL 缺行跳过;
- 格式化输出与异常透传(不吞 MilvusUnavailable / EmbeddingError)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.kb.retriever as retriever
from app.kb.embedding import EmbeddingError
from app.kb.milvus import MilvusUnavailable
from app.kb.store import StoredChunk


def _stored(
    chunk_id: int,
    *,
    category: str = "售后",
    questions: str = "问",
    answer: str = "答",
    content_type: str = "qa",
    section_path: str = "FAQ/售后",
) -> StoredChunk:
    """构造一条已落库切片的只读快照(只填检索用得到的字段)。"""
    return StoredChunk(
        id=chunk_id,
        category=category,
        questions=questions,
        answer=answer,
        section_path=section_path,
        content_type=content_type,
        is_key_clause=False,
        vectorize_status="done",
        vector_id=str(chunk_id),
        prev_chunk_id=None,
        next_chunk_id=None,
    )


class FakeMilvus:
    """只实现 search 的假 Milvus,记录入参并返回预设命中。"""

    def __init__(self, hits: list[tuple[str, float]]) -> None:
        self.hits = list(hits)
        self.calls: list[dict] = []

    def search(self, vector, limit: int) -> list[tuple[str, float]]:
        self.calls.append({"vector": vector, "limit": limit})
        return list(self.hits)


class FakeStore:
    """只实现 fetch_by_ids 的假仓储,按 id 过滤预设行。"""

    def __init__(self, rows: list[StoredChunk]) -> None:
        self.rows = {row.id: row for row in rows}
        self.calls: list[list[int]] = []

    def fetch_by_ids(self, ids):
        self.calls.append(list(ids))
        return {
            chunk_id: self.rows[chunk_id]
            for chunk_id in ids
            if chunk_id in self.rows
        }


def _patch_embedding(monkeypatch, vectors: list[list[float]]) -> list[list[str]]:
    """把 embed_texts 换成记录调用并返回固定向量的假实现。"""
    calls: list[list[str]] = []

    def fake_embed(texts):
        calls.append(list(texts))
        return vectors

    monkeypatch.setattr(retriever, "embed_texts", fake_embed)
    return calls


def test_empty_query_returns_no_match_without_embedding(monkeypatch):
    """空白 query 直接返回无匹配文案,且不调用 embedding / Milvus。"""
    called = []
    monkeypatch.setattr(
        retriever,
        "embed_texts",
        lambda texts: called.append(texts) or [[0.0] * 1024],
    )
    fake_milvus = FakeMilvus([])
    monkeypatch.setattr(retriever, "milvus", fake_milvus)

    assert "没有找到" in retriever.retrieve("   ")
    assert retriever.search_hits("") == []
    assert called == []
    assert fake_milvus.calls == []


def test_filters_by_min_score_and_preserves_order(monkeypatch):
    """分数低于 min_score 的命中被过滤,其余命中保持 Milvus 返回顺序。"""
    _patch_embedding(monkeypatch, [[0.0] * 1024])
    fake_milvus = FakeMilvus([("2", 0.81), ("1", 0.49)])
    monkeypatch.setattr(retriever, "milvus", fake_milvus)
    monkeypatch.setattr(
        retriever,
        "store",
        FakeStore([_stored(1), _stored(2)]),
    )

    hits = retriever.search_hits("邮费是多少", top_k=3, min_score=0.5)

    assert [hit.chunk_id for hit in hits] == [2]
    assert fake_milvus.calls == [{"vector": [0.0] * 1024, "limit": 3}]


def test_preserves_hit_order_and_maps_all_fields(monkeypatch):
    """多条命中按 Milvus 顺序(而非 id 顺序)回表并完整映射字段。"""
    _patch_embedding(monkeypatch, [[0.5] * 1024])
    monkeypatch.setattr(
        retriever,
        "milvus",
        FakeMilvus([("3", 0.9), ("1", 0.7), ("2", 0.6)]),
    )
    monkeypatch.setattr(
        retriever,
        "store",
        FakeStore(
            [
                _stored(
                    1,
                    category="退货",
                    questions="退货怎么操作?",
                    answer="申请售后并寄回。",
                    content_type="policy",
                    section_path="FAQ/售后/退货",
                ),
                _stored(2, questions="尺码怎么选?", answer="按胸围选。"),
                _stored(3, questions="多久发货?", answer="48 小时内。"),
            ]
        ),
    )

    hits = retriever.search_hits("怎么退货", top_k=3, min_score=0.5)

    assert [hit.chunk_id for hit in hits] == [3, 1, 2]
    first = hits[1]
    assert first.score == pytest.approx(0.7)
    assert first.category == "退货"
    assert first.questions == "退货怎么操作?"
    assert first.answer == "申请售后并寄回。"
    assert first.content_type == "policy"
    assert first.section_path == "FAQ/售后/退货"


def test_defaults_come_from_settings_and_include_boundary_score(monkeypatch):
    """默认 top_k / min_score 运行时取 Settings;score == min_score 也保留。"""
    _patch_embedding(monkeypatch, [[0.0] * 1024])
    fake_milvus = FakeMilvus([("1", 0.5), ("2", 0.49)])
    monkeypatch.setattr(retriever, "milvus", fake_milvus)
    monkeypatch.setattr(retriever, "store", FakeStore([_stored(1), _stored(2)]))
    monkeypatch.setattr(
        retriever,
        "get_settings",
        lambda: SimpleNamespace(faq_max_results=3, knowledge_min_score=0.5),
    )

    hits = retriever.search_hits("运费怎么算")

    assert [hit.chunk_id for hit in hits] == [1]
    assert fake_milvus.calls[0]["limit"] == 3


def test_retrieve_formats_question_answer_blocks(monkeypatch):
    """retrieve 输出「序号. 问题 + → 答案」,条目之间空行分隔。"""
    _patch_embedding(monkeypatch, [[0.0] * 1024])
    monkeypatch.setattr(retriever, "milvus", FakeMilvus([("1", 0.9)]))
    monkeypatch.setattr(
        retriever,
        "store",
        FakeStore(
            [_stored(1, questions="运费怎么算?", answer="满 99 包邮。")]
        ),
    )

    assert retriever.retrieve("运费怎么算") == "1. 运费怎么算?\n→ 满 99 包邮。"


def test_retrieve_joins_multiple_blocks_with_blank_line(monkeypatch):
    """多条命中按 1、2 编号,块之间用两个换行连接。"""
    _patch_embedding(monkeypatch, [[0.0] * 1024])
    monkeypatch.setattr(
        retriever,
        "milvus",
        FakeMilvus([("1", 0.9), ("2", 0.8)]),
    )
    monkeypatch.setattr(
        retriever,
        "store",
        FakeStore(
            [
                _stored(1, questions="运费怎么算?", answer="满 99 包邮。"),
                _stored(2, questions="多久发货?", answer="48 小时内。"),
            ]
        ),
    )

    assert retriever.retrieve("邮费") == (
        "1. 运费怎么算?\n→ 满 99 包邮。\n\n2. 多久发货?\n→ 48 小时内。"
    )


def test_no_match_returns_faq_not_found_text(monkeypatch):
    """非空 query 无命中时返回与旧 query_faq 一致的无匹配文案。"""
    _patch_embedding(monkeypatch, [[0.0] * 1024])
    monkeypatch.setattr(retriever, "milvus", FakeMilvus([]))
    monkeypatch.setattr(retriever, "store", FakeStore([]))

    assert (
        retriever.retrieve("邮费")
        == "FAQ 库中没有找到与「邮费」相关的条目"
    )


def test_missing_mysql_rows_are_skipped_with_warning(monkeypatch, caplog):
    """Milvus 有命中但 MySQL 查不到时跳过该 id,并记录 warning。"""
    _patch_embedding(monkeypatch, [[0.0] * 1024])
    monkeypatch.setattr(
        retriever,
        "milvus",
        FakeMilvus([("1", 0.9), ("99", 0.8), ("2", 0.7)]),
    )
    monkeypatch.setattr(retriever, "store", FakeStore([_stored(1), _stored(2)]))

    with caplog.at_level("WARNING", logger=retriever.logger.name):
        hits = retriever.search_hits("退货", top_k=3, min_score=0.5)

    assert [hit.chunk_id for hit in hits] == [1, 2]
    assert "99" in caplog.text


def test_embedding_error_propagates(monkeypatch):
    """EmbeddingError 不被吞掉,直接冒泡给执行器。"""

    def fail(texts):
        raise EmbeddingError("embedding down")

    monkeypatch.setattr(retriever, "embed_texts", fail)
    with pytest.raises(EmbeddingError, match="embedding down"):
        retriever.search_hits("退货")


def test_milvus_unavailable_propagates(monkeypatch):
    """MilvusUnavailable 不被吞掉,直接冒泡给执行器。"""
    _patch_embedding(monkeypatch, [[0.0] * 1024])

    class BrokenMilvus:
        def search(self, vector, limit):
            raise MilvusUnavailable("milvus down")

    monkeypatch.setattr(retriever, "milvus", BrokenMilvus())
    with pytest.raises(MilvusUnavailable, match="milvus down"):
        retriever.search_hits("退货")
