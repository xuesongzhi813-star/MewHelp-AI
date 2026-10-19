"""MySQL 知识仓储测试(Task 7):真实连库 + 外层事务 SAVEPOINT 回滚。

与 tests/test_tools.py 同一套路:把 ``app.kb.store.get_session_local`` 替换成
绑定外层事务连接的 sessionmaker(join_transaction_mode="create_savepoint")。
仓储内部自行 ``session.begin()`` 只会开 SAVEPOINT,用例结束整体 rollback,
knowledge_chunks / qa_extraction_staging 不留任何脏数据。
"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import app.kb.store as store
from app.db import get_engine
from app.kb.documents import chunk_fingerprint, normalize_text
from app.models import Conversation, Message


@pytest.fixture
def kb_tx(monkeypatch):
    """注入回滚会话工厂:仓储的写操作最终随外层事务整体 rollback。"""
    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(store, "get_session_local", lambda: session_local)
        try:
            yield conn
        finally:
            outer.rollback()


def _seed_conversation(conn, user_id: str) -> int:
    """在同一回滚事务里建一条真实 Conversation,返回自增 id。"""
    with Session(conn) as session:
        with session.begin():
            conversation = Conversation(user_id=user_id)
            session.add(conversation)
            session.flush()
            return conversation.id


def _add_message(conn, conversation_id: int, role: str, content: str | None) -> int:
    """在同一回滚事务里追加一条消息,返回自增 id。"""
    with Session(conn) as session:
        with session.begin():
            message = Message(conversation_id=conversation_id, role=role, content=content)
            session.add(message)
            session.flush()
            return message.id


def _candidate(questions="问1", answer="答1", **overrides) -> store.ChunkCandidate:
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


# ---------------------------------------------------------------------------
# knowledge_chunks
# ---------------------------------------------------------------------------


def test_insert_pending_and_link_siblings(kb_tx):
    ids = store.insert_chunks(
        [
            _candidate("问1", "答1", section_path="售后 > 节1"),
            _candidate("问2", "答2", section_path="售后 > 节2"),
        ]
    )
    assert isinstance(ids, list) and len(ids) == 2
    assert all(isinstance(chunk_id, int) for chunk_id in ids)

    store.link_siblings(ids)
    rows = store.fetch_by_ids(ids)
    assert set(rows) == set(ids)
    assert rows[ids[0]].next_chunk_id == ids[1]
    assert rows[ids[0]].prev_chunk_id is None
    assert rows[ids[1]].prev_chunk_id == ids[0]
    assert rows[ids[1]].next_chunk_id is None
    assert store.iter_pending()[0].id == ids[0]

    # 新行默认 pending,vector_id 为空
    first = rows[ids[0]]
    assert first.vectorize_status == "pending"
    assert first.vector_id is None
    assert first.category == "售后"
    assert first.questions == "问1"
    assert first.answer == "答1"
    assert first.section_path == "售后 > 节1"
    assert first.content_type == "文档章节"
    assert first.is_key_clause is False


def test_insert_chunks_accepts_injected_session_and_only_flushes(kb_tx):
    """传入 session 时仓储只 flush 拿 id,不 commit;同会话内立即可见。"""
    with Session(kb_tx) as session:
        with session.begin():
            ids = store.insert_chunks([_candidate("注入问", "注入答")], session=session)
            assert session.get(store.KnowledgeChunk, ids[0]) is not None
            store.mark_done(ids, session=session)
            row = session.get(store.KnowledgeChunk, ids[0])
            assert row.vectorize_status == "done"
            assert row.vector_id == str(ids[0])


def test_iter_pending_respects_limit_and_id_order(kb_tx):
    ids = store.insert_chunks([_candidate(f"问{i}", f"答{i}") for i in range(3)])
    pending = store.iter_pending(limit=2)
    assert [item.id for item in pending] == ids[:2]
    assert isinstance(pending[0], store.PendingChunk)
    assert pending[0].category == "售后"
    assert pending[0].questions == "问0"
    assert pending[0].answer == "答0"


def test_mark_done_and_overview_counts(kb_tx):
    ids = store.insert_chunks([_candidate("问", "答", section_path="路径")])
    store.mark_done(ids)

    row = store.fetch_by_ids(ids)[ids[0]]
    assert row.vectorize_status == "done"
    assert row.vector_id == str(ids[0])
    assert store.done_ids() == [str(ids[0])]
    assert store.iter_pending() == []

    counts = store.overview_counts()
    assert counts["done"] == 1 and counts["pending"] == 0
    assert counts["chunks"] == 1
    assert counts["key_clauses"] == 0


def test_overview_key_clauses_counts_true_rows(kb_tx):
    store.insert_chunks(
        [
            _candidate("普通问", "普通答", is_key_clause=False),
            _candidate("重要问", "重要答", is_key_clause=True),
        ]
    )
    counts = store.overview_counts()
    assert counts["chunks"] == 2
    assert counts["pending"] == 2
    assert counts["key_clauses"] == 1


def test_existing_fingerprints_uses_questions_and_answer(kb_tx):
    store.insert_chunks(
        [_candidate("怎么 退货?", "7 天无理由。", section_path="路径")]
    )
    from app.kb.documents import chunk_fingerprint

    assert chunk_fingerprint("怎么退货", "7天无理由") in store.existing_fingerprints()


def test_existing_fingerprints_empty_table(kb_tx):
    assert store.existing_fingerprints() == set()


def test_existing_questions_normalized(kb_tx):
    store.insert_chunks([_candidate("怎么 退货?", "7 天无理由。")])
    expected = normalize_text("怎么 退货?")
    assert store.existing_questions_normalized() == {expected}
    assert "怎么退货" in store.existing_questions_normalized()


def test_fetch_by_ids_empty_returns_empty_dict(kb_tx):
    assert store.fetch_by_ids([]) == {}
    assert store.mark_done([]) is None
    assert store.link_siblings([]) is None


def test_list_recent_returns_newest_first(kb_tx):
    ids = store.insert_chunks([_candidate(f"问{i}", f"答{i}") for i in range(3)])
    recent = store.list_recent(limit=2)
    assert [row.id for row in recent] == [ids[2], ids[1]]
    assert isinstance(recent[0], store.StoredChunk)


def test_reset_all_pending_returns_rows_to_pending(kb_tx):
    ids = store.insert_chunks([_candidate("问", "答")])
    store.mark_done(ids)
    assert store.overview_counts()["done"] == 1

    store.reset_all_pending()

    row = store.fetch_by_ids(ids)[ids[0]]
    assert row.vectorize_status == "pending"
    assert row.vector_id is None
    assert store.done_ids() == []
    assert store.overview_counts()["pending"] == 1


def test_delete_all_chunks_clears_rows_and_sibling_pointers(kb_tx):
    ids = store.insert_chunks([_candidate("问1", "答1"), _candidate("问2", "答2")])
    store.link_siblings(ids)

    store.delete_all_chunks()

    assert store.overview_counts()["chunks"] == 0
    assert store.fetch_by_ids(ids) == {}
    assert store.iter_pending() == []


# ---------------------------------------------------------------------------
# qa_extraction_staging
# ---------------------------------------------------------------------------


def test_staging_roundtrip(kb_tx):
    ids = store.insert_staging(
        [store.StagingCandidate("B1", "conv:1:msgs:1-3", "问", "答")]
    )
    assert len(ids) == 1

    counts = store.staging_counts()
    assert counts["extracted"] == 1
    assert counts["kept"] == 0
    assert counts["discarded"] == 0
    assert counts["batches"] == 1

    store.mark_staging(ids, "kept")

    rows = store.list_staging("kept")
    assert len(rows) == 1
    assert rows[0].id == ids[0]
    assert rows[0].batch_no == "B1"
    assert rows[0].source_ref == "conv:1:msgs:1-3"
    assert rows[0].question == "问"
    assert rows[0].answer == "答"
    assert rows[0].status == "kept"
    assert rows[0].created_at is not None

    counts = store.staging_counts()
    assert counts == {"extracted": 0, "kept": 1, "discarded": 0, "batches": 1}


def test_insert_staging_preserves_input_order_and_defaults(kb_tx):
    ids = store.insert_staging(
        [
            store.StagingCandidate("B1", None, "问1", "答1"),
            store.StagingCandidate("B2", "conv:9:msgs:5-8", "问2", "答2"),
        ]
    )
    rows = {row.id: row for row in store.list_staging()}
    assert rows[ids[0]].question == "问1"
    assert rows[ids[0]].source_ref is None
    assert rows[ids[0]].status == "extracted"
    assert rows[ids[1]].question == "问2"
    # 两个批次
    assert store.staging_counts()["batches"] == 2


def test_list_staging_filters_status_and_limit(kb_tx):
    ids = store.insert_staging(
        [
            store.StagingCandidate("B1", None, "问1", "答1"),
            store.StagingCandidate("B1", None, "问2", "答2"),
            store.StagingCandidate("B1", None, "问3", "答3"),
        ]
    )
    store.mark_staging([ids[0]], "discarded")
    store.mark_staging([ids[1]], "kept")

    assert [row.id for row in store.list_staging("discarded")] == [ids[0]]
    assert [row.id for row in store.list_staging("kept")] == [ids[1]]
    assert len(store.list_staging()) == 3
    assert len(store.list_staging(limit=2)) == 2
    assert store.list_staging(limit=0) == []


def test_insert_staging_empty_returns_empty_list(kb_tx):
    assert store.insert_staging([]) == []
    assert store.mark_staging([], "kept") is None


# ---------------------------------------------------------------------------
# 对话挖掘素材
# ---------------------------------------------------------------------------


def test_list_conversations_for_mining_filters_and_orders(kb_tx):
    conv_a = _seed_conversation(kb_tx, "mining-a")
    user_id = _add_message(kb_tx, conv_a, "user", "怎么退货?")
    tool_id = _add_message(kb_tx, conv_a, "tool", "工具输出不应入 turns")
    _add_message(kb_tx, conv_a, "assistant", "   ")  # 空内容忽略
    assistant_id = _add_message(kb_tx, conv_a, "assistant", "7 天无理由。")

    # 只有 user、没有 assistant 的会话不返回
    conv_b = _seed_conversation(kb_tx, "mining-b")
    _add_message(kb_tx, conv_b, "user", "只有我")

    # 只有 assistant 的会话不返回
    conv_c = _seed_conversation(kb_tx, "mining-c")
    _add_message(kb_tx, conv_c, "assistant", "自问自答")

    conversations = store.list_conversations_for_mining()

    assert [item.id for item in conversations] == [conv_a]
    item = conversations[0]
    assert item.user_id == "mining-a"
    # max_message_id 取会话内所有消息(含 tool / 空内容)的最大 id
    assert item.max_message_id == max(user_id, tool_id, assistant_id)
    assert isinstance(item.turns, tuple)
    assert item.turns == (
        (user_id, "user", "怎么退货?"),
        (assistant_id, "assistant", "7 天无理由。"),
    )


def test_list_conversations_for_mining_empty_db(kb_tx):
    assert store.list_conversations_for_mining() == []


def test_staging_tail_parses_max_last_message_id(kb_tx):
    store.insert_staging(
        [
            store.StagingCandidate("B1", "conv:7:msgs:1-3", "问1", "答1"),
            store.StagingCandidate("B2", "conv:7:msgs:4-9", "问2", "答2"),
            store.StagingCandidate("B2", "conv:70:msgs:4-99", "问3", "答3"),
            store.StagingCandidate("B3", "manual:ref", "问4", "答4"),
        ]
    )
    assert store.staging_tail(7) == 9
    assert store.staging_tail(70) == 99
    assert store.staging_tail(1) is None
    assert store.staging_tail(8) is None
