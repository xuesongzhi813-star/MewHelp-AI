"""演示会话种子测试(Task 11):真实 MySQL 回滚。

用例开始前在同一事务里清掉所有 ``seed-ch03-`` 前缀的会话,
保证计数与内容断言不受已提交 seed 数据影响;结束整体 rollback。
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

import app.kb.seed_conversations as seed_conversations
from app.db import get_engine
from app.models import Conversation, Message, Ticket


@pytest.fixture
def seed_tx(monkeypatch):
    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(
            seed_conversations, "get_session_local", lambda: session_local
        )
        # 事务内清掉既有 seed 会话(含消息),rollback 后原样恢复。
        with Session(conn) as session:
            with session.begin():
                ids = session.scalars(
                    select(Conversation.id).where(
                        Conversation.user_id.like("seed-ch03-%")
                    )
                ).all()
                if ids:
                    # 先删引用 conversations 的 tickets,再删 messages,
                    # 避免真实工单存在时触发外键错误。
                    session.execute(
                        delete(Ticket).where(Ticket.conversation_id.in_(ids))
                    )
                    session.execute(
                        delete(Message).where(Message.conversation_id.in_(ids))
                    )
                    session.execute(
                        delete(Conversation).where(Conversation.id.in_(ids))
                    )
        try:
            yield conn
        finally:
            outer.rollback()


def _seed_rows(conn):
    """返回 {user_id: [(role, content), ...]},只含 seed-ch03- 前缀会话。"""
    with Session(conn) as session:
        with session.begin():
            conversations = session.execute(
                select(Conversation.id, Conversation.user_id)
                .where(Conversation.user_id.like("seed-ch03-%"))
                .order_by(Conversation.id)
            ).all()
            result = {}
            for conversation_id, user_id in conversations:
                messages = session.execute(
                    select(Message.role, Message.content)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.id)
                ).all()
                result[user_id] = [(role, content) for role, content in messages]
            return result


def test_main_inserts_three_demo_conversations(seed_tx, capsys):
    assert seed_conversations.main() == 0

    rows = _seed_rows(seed_tx)
    assert len(rows) == 3
    assert all(user_id.startswith("seed-ch03-") for user_id in rows)

    for messages in rows.values():
        roles = {role for role, _ in messages}
        assert roles == {"user", "assistant"}
        assert len(messages) >= 2

    text = "\n".join(
        content for messages in rows.values() for _, content in messages
    )
    # 覆盖售后 / 物流运费 / 订单一次性信息 / 寒暄
    for keyword in ("换货", "运费", "发货", "订单", "你好"):
        assert keyword in text
    # 消息内容不得为空
    assert all(content.strip() for messages in rows.values() for _, content in messages)
    assert "新插入 3 组" in capsys.readouterr().out


def test_main_is_idempotent_by_user_id(seed_tx, capsys):
    assert seed_conversations.main() == 0
    first = _seed_rows(seed_tx)
    first_counts = {user_id: len(messages) for user_id, messages in first.items()}

    assert seed_conversations.main() == 0

    second = _seed_rows(seed_tx)
    assert set(second) == set(first)
    assert {user_id: len(messages) for user_id, messages in second.items()} == (
        first_counts
    )
    assert "新插入 0 组" in capsys.readouterr().out


def test_existing_conversation_with_same_user_id_is_skipped(seed_tx):
    """只要同 user_id 已有会话,整组跳过,不追加任何消息。"""
    with Session(seed_tx) as session:
        with session.begin():
            existing = Conversation(user_id="seed-ch03-aftersales")
            session.add(existing)
            session.flush()
            session.add(
                Message(
                    conversation_id=existing.id,
                    role="user",
                    content="我已经存在的会话",
                )
            )

    assert seed_conversations.main() == 0

    rows = _seed_rows(seed_tx)
    assert len(rows) == 3  # 其余两组仍然插入
    assert rows["seed-ch03-aftersales"] == [("user", "我已经存在的会话")]


def test_seed_function_does_not_commit_injected_session(seed_tx):
    """种子函数只 flush,不 commit;事务边界归调用方。"""
    from sqlalchemy import event

    commits: list[str] = []

    def on_before_commit(session):
        commits.append("committed")

    with Session(seed_tx) as session:
        with session.begin():
            event.listen(session, "before_commit", on_before_commit)
            try:
                inserted = seed_conversations.seed_demo_conversations(session)
                assert inserted == 3
                assert commits == []
                assert session.in_transaction()
            finally:
                event.remove(session, "before_commit", on_before_commit)
