"""MySQL 知识仓储测试(Task 7):真实连库 + 外层事务 SAVEPOINT 回滚。

与 tests/test_tools.py 同一套路:把 ``app.kb.store.get_session_local`` 替换成
绑定外层事务连接的 sessionmaker(join_transaction_mode="create_savepoint")。
仓储内部自行 ``session.begin()`` 只会开 SAVEPOINT,用例结束整体 rollback,
knowledge_chunks / qa_extraction_staging 不留任何脏数据。

隔离约定:回滚只能撤销本用例的写入,无法隐藏其他测试或 seed 脚本已提交的行。
因此除全局语义操作(delete_all_chunks)外,断言一律用:
- 操作前后的计数增量;
- 按本用例创建的自增 id / 唯一标识过滤后的子集;
- 唯一 user_id 前缀过滤会话。
"""

import uuid

import pytest
from sqlalchemy import event
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


def _unique(scope: str) -> str:
    """每条用例唯一的标识,避免与其他已提交数据串号。"""
    return f"test-kb-store-{scope}-{uuid.uuid4().hex}"


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

    pending_ids = [item.id for item in store.iter_pending(limit=10**9)]
    assert [chunk_id for chunk_id in pending_ids if chunk_id in set(ids)] == ids

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
    # created_at 由数据库默认值填充,快照必须带上。
    assert first.created_at is not None


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


def test_write_functions_never_commit_injected_session(kb_tx):
    """传入 session 时写函数绝不提交:before_commit 监听器一次都不触发。

    事务边界归调用方;若仓储内部误 commit,监听器会立刻记录,用例变红。
    调用后事务仍处于活动状态(回滚/提交仍由外层决定)。
    """
    commits: list[str] = []

    def on_before_commit(session: Session) -> None:
        commits.append("committed")

    with Session(kb_tx) as session:
        with session.begin():
            event.listen(session, "before_commit", on_before_commit)
            try:
                ids = store.insert_chunks(
                    [_candidate("不提交问", "不提交答")], session=session
                )
                store.link_siblings(ids, session=session)
                store.mark_done(ids, session=session)
                staging_ids = store.insert_staging(
                    [store.StagingCandidate(_unique("batch"), None, "问", "答")],
                    session=session,
                )
                store.mark_staging(staging_ids, "kept", session=session)
                # 全表写操作同样必须只作用于当前事务,绝不提交
                store.reset_all_pending(session=session)
                store.delete_all_chunks(session=session)

                assert commits == []
                assert session.in_transaction()
            finally:
                # 在 session.begin() 上下文退出提交之前摘掉监听器,
                # 只观测仓储函数是否提交,不干扰外层事务收尾。
                event.remove(session, "before_commit", on_before_commit)


def test_iter_pending_respects_limit_and_id_order(kb_tx):
    before = [item.id for item in store.iter_pending(limit=10**9)]
    ids = store.insert_chunks([_candidate(f"问{i}", f"答{i}") for i in range(3)])

    pending = store.iter_pending(limit=len(before) + 2)
    assert [item.id for item in pending] == before + ids[:2]

    first_new = pending[len(before)]
    assert isinstance(first_new, store.PendingChunk)
    assert first_new.id == ids[0]
    assert first_new.category == "售后"
    assert first_new.questions == "问0"
    assert first_new.answer == "答0"


def test_mark_done_and_overview_counts(kb_tx):
    before = store.overview_counts()
    before_done = set(store.done_ids())

    ids = store.insert_chunks([_candidate("问", "答", section_path="路径")])
    store.mark_done(ids)

    row = store.fetch_by_ids(ids)[ids[0]]
    assert row.vectorize_status == "done"
    assert row.vector_id == str(ids[0])

    assert set(store.done_ids()) - before_done == {str(ids[0])}
    assert ids[0] not in [item.id for item in store.iter_pending(limit=10**9)]

    counts = store.overview_counts()
    assert counts["chunks"] == before["chunks"] + 1
    assert counts["done"] == before["done"] + 1
    # 插入 pending 后立即标记 done:待处理总数净变化为 0
    assert counts["pending"] == before["pending"]
    assert counts["key_clauses"] == before["key_clauses"]


def test_overview_key_clauses_counts_true_rows(kb_tx):
    before = store.overview_counts()
    store.insert_chunks(
        [
            _candidate("普通问", "普通答", is_key_clause=False),
            _candidate("重要问", "重要答", is_key_clause=True),
        ]
    )
    counts = store.overview_counts()
    assert counts["chunks"] == before["chunks"] + 2
    assert counts["pending"] == before["pending"] + 2
    assert counts["key_clauses"] == before["key_clauses"] + 1


def test_existing_fingerprints_uses_questions_and_answer(kb_tx):
    token = _unique("fingerprint")
    before = store.existing_fingerprints()

    store.insert_chunks(
        [
            _candidate(
                f"怎么 退货 {token}?",
                f"7 天无理由。{token}",
                section_path="路径",
            )
        ]
    )

    expected = chunk_fingerprint(f"怎么退货{token}", f"7天无理由{token}")
    after = store.existing_fingerprints()
    assert expected in after
    # 仅新增这一条指纹,不受库里已有数据影响
    assert after - before == {expected}


def test_existing_questions_normalized(kb_tx):
    token = _unique("normalize")
    question = f"怎么 退货 {token}?"
    before = store.existing_questions_normalized()

    store.insert_chunks([_candidate(question, "7 天无理由。")])

    expected = normalize_text(question)
    after = store.existing_questions_normalized()
    assert expected in after
    assert after - before == {expected}


def test_fetch_by_ids_empty_returns_empty_dict(kb_tx):
    assert store.fetch_by_ids([]) == {}
    assert store.mark_done([]) is None
    assert store.link_siblings([]) is None


def test_list_recent_returns_newest_first(kb_tx):
    ids = store.insert_chunks([_candidate(f"问{i}", f"答{i}") for i in range(3)])
    recent = [row for row in store.list_recent(limit=20) if row.id in set(ids)]
    # 新插入行自增 id 最大,按 id 倒序应排在最前
    assert [row.id for row in recent] == [ids[2], ids[1], ids[0]]
    assert isinstance(recent[0], store.StoredChunk)
    # spec §16:recent[] 需要 created_at;真实行必须非空。
    assert recent[0].created_at is not None


def test_reset_all_pending_returns_rows_to_pending(kb_tx):
    ids = store.insert_chunks([_candidate("问", "答")])
    store.mark_done(ids)
    assert store.fetch_by_ids(ids)[ids[0]].vectorize_status == "done"

    store.reset_all_pending()

    row = store.fetch_by_ids(ids)[ids[0]]
    assert row.vectorize_status == "pending"
    assert row.vector_id is None
    assert str(ids[0]) not in store.done_ids()

    # reset_all_pending 是全局操作:不假设库里只有本用例的行,
    # 直接断言重置后的全局不变量——没有任何 done,且全部切片都是 pending。
    counts = store.overview_counts()
    assert counts["done"] == 0
    assert counts["pending"] == counts["chunks"]


def test_delete_all_chunks_clears_rows_and_sibling_pointers(kb_tx):
    ids = store.insert_chunks([_candidate("问1", "答1"), _candidate("问2", "答2")])
    store.link_siblings(ids)

    store.delete_all_chunks()

    # delete_all 的语义就是清空全表,因此这里断言全局 0 是操作本身要验证的行为;
    # 回滚后其他已提交行会原样恢复。
    assert store.overview_counts()["chunks"] == 0
    assert store.fetch_by_ids(ids) == {}
    assert store.iter_pending() == []


# ---------------------------------------------------------------------------
# qa_extraction_staging
# ---------------------------------------------------------------------------


def test_staging_roundtrip(kb_tx):
    before = store.staging_counts()
    batch_no = _unique("batch")
    source_ref = f"conv:{uuid.uuid4().int % 10**12}:msgs:1-3"

    ids = store.insert_staging(
        [store.StagingCandidate(batch_no, source_ref, "问", "答")]
    )
    assert len(ids) == 1

    counts = store.staging_counts()
    assert counts["extracted"] == before["extracted"] + 1
    assert counts["kept"] == before["kept"]
    assert counts["discarded"] == before["discarded"]
    assert counts["batches"] == before["batches"] + 1

    store.mark_staging(ids, "kept")

    rows = [row for row in store.list_staging("kept") if row.id == ids[0]]
    assert len(rows) == 1
    row = rows[0]
    assert row.batch_no == batch_no
    assert row.source_ref == source_ref
    assert row.question == "问"
    assert row.answer == "答"
    assert row.status == "kept"
    assert row.created_at is not None

    counts = store.staging_counts()
    assert counts["extracted"] == before["extracted"]
    assert counts["kept"] == before["kept"] + 1
    assert counts["discarded"] == before["discarded"]
    assert counts["batches"] == before["batches"] + 1


def test_insert_staging_preserves_input_order_and_defaults(kb_tx):
    before = store.staging_counts()
    batch_one = _unique("batch")
    batch_two = _unique("batch")

    ids = store.insert_staging(
        [
            store.StagingCandidate(batch_one, None, "问1", "答1"),
            store.StagingCandidate(batch_two, "conv:9:msgs:5-8", "问2", "答2"),
        ]
    )
    rows = {row.id: row for row in store.list_staging(limit=50) if row.id in set(ids)}
    assert rows[ids[0]].question == "问1"
    assert rows[ids[0]].source_ref is None
    assert rows[ids[0]].status == "extracted"
    assert rows[ids[1]].question == "问2"

    after = store.staging_counts()
    assert after["extracted"] == before["extracted"] + 2
    assert after["batches"] == before["batches"] + 2


def test_list_staging_filters_status_and_limit(kb_tx):
    batch_no = _unique("batch")
    ids = store.insert_staging(
        [
            store.StagingCandidate(batch_no, None, "问1", "答1"),
            store.StagingCandidate(batch_no, None, "问2", "答2"),
            store.StagingCandidate(batch_no, None, "问3", "答3"),
        ]
    )
    store.mark_staging([ids[0]], "discarded")
    store.mark_staging([ids[1]], "kept")

    assert [row.id for row in store.list_staging("discarded") if row.id in set(ids)] == [
        ids[0]
    ]
    assert [row.id for row in store.list_staging("kept") if row.id in set(ids)] == [
        ids[1]
    ]
    # 新行 id 最大,按 id 倒序 limit=2 恰好是最近两条
    assert [row.id for row in store.list_staging(limit=2)] == [ids[2], ids[1]]
    assert store.list_staging(limit=0) == []


def test_insert_staging_empty_returns_empty_list(kb_tx):
    assert store.insert_staging([]) == []
    assert store.mark_staging([], "kept") is None


# ---------------------------------------------------------------------------
# 对话挖掘素材
# ---------------------------------------------------------------------------


def test_list_conversations_for_mining_filters_and_orders(kb_tx):
    prefix = _unique("mining")
    conv_a = _seed_conversation(kb_tx, f"{prefix}-a")
    user_id = _add_message(kb_tx, conv_a, "user", "怎么退货?")
    tool_id = _add_message(kb_tx, conv_a, "tool", "工具输出不应入 turns")
    _add_message(kb_tx, conv_a, "assistant", "   ")  # 空内容忽略
    assistant_id = _add_message(kb_tx, conv_a, "assistant", "7 天无理由。")

    # 只有 user、没有 assistant 的会话不返回
    conv_b = _seed_conversation(kb_tx, f"{prefix}-b")
    _add_message(kb_tx, conv_b, "user", "只有我")

    # 只有 assistant 的会话不返回
    conv_c = _seed_conversation(kb_tx, f"{prefix}-c")
    _add_message(kb_tx, conv_c, "assistant", "自问自答")

    # 只取本用例唯一前缀的会话,不受库里其他会话影响
    conversations = [
        item
        for item in store.list_conversations_for_mining()
        if item.user_id.startswith(prefix)
    ]

    assert [item.id for item in conversations] == [conv_a]
    item = conversations[0]
    assert item.user_id == f"{prefix}-a"
    # max_message_id 取会话内所有消息(含 tool / 空内容)的最大 id
    assert item.max_message_id == max(user_id, tool_id, assistant_id)
    assert isinstance(item.turns, tuple)
    assert item.turns == (
        (user_id, "user", "怎么退货?"),
        (assistant_id, "assistant", "7 天无理由。"),
    )


def test_list_conversations_for_mining_unrelated_ids_excluded(kb_tx):
    """唯一前缀下没有会话:全局结果再多也不会误入本用例的断言范围。"""
    prefix = _unique("no-conv")
    assert [
        item
        for item in store.list_conversations_for_mining()
        if item.user_id.startswith(prefix)
    ] == []


def test_staging_tail_parses_max_last_message_id(kb_tx):
    # 用到 15 位随机会话 id,避开既有自增会话与 staging 引用
    conversation_id = 10**14 + uuid.uuid4().int % 10**10
    neighboring_id = int(f"{conversation_id}0")

    store.insert_staging(
        [
            store.StagingCandidate(
                _unique("batch"),
                f"conv:{conversation_id}:msgs:1-3",
                "问1",
                "答1",
            ),
            store.StagingCandidate(
                _unique("batch"),
                f"conv:{conversation_id}:msgs:4-9",
                "问2",
                "答2",
            ),
            store.StagingCandidate(
                _unique("batch"),
                f"conv:{neighboring_id}:msgs:4-99",
                "问3",
                "答3",
            ),
            store.StagingCandidate(_unique("batch"), "manual:ref", "问4", "答4"),
        ]
    )

    assert store.staging_tail(conversation_id) == 9
    # 前缀相似(70 vs 700)不能串号
    assert store.staging_tail(neighboring_id) == 99
    assert store.staging_tail(conversation_id + 1) is None
