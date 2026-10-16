"""MySQLSessionStore 测试:落库/重建多轮历史(含工具调用消息)。

与 test_tools.py 同款「外层事务 + SAVEPOINT」隔离:把
app.session_store.get_session_local 替换成绑定外层事务连接的会话工厂,
store 内部 session.begin() 因 join_transaction_mode="create_savepoint"
只开 SAVEPOINT,用例结束整体 rollback,真实连库但零脏数据。
"""

import uuid

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import app.session_store as store_mod
from app.db import get_engine
from app.history import SessionStore
from app.models import Conversation, Message
from app.session_store import MySQLSessionStore


@pytest.fixture
def store_tx(monkeypatch):
    """注入回滚会话工厂:store 的所有读写最终随外层事务整体 rollback。

    yield 连接本身,用例内可再开 Session 做预插/断言(同一连接可见未提交数据)。
    """
    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(store_mod, "get_session_local", lambda: session_local)
        try:
            yield conn
        finally:
            outer.rollback()


def _new_sid(prefix: str) -> str:
    """每次生成全局唯一 session_id,避免与库中既有/其他用例数据串扰。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------- 新会话 ----------


def test_new_session_history_empty_and_conversation_created(store_tx):
    """全新 session_id:get_history 返回 [],且已自动建好一条「进行中」会话行。"""
    store = MySQLSessionStore()
    sid = _new_sid("store-new")

    assert store.get_history(sid) == []

    # savepoint 内断言:get_or_create 已经落了会话行
    with Session(store_tx) as session:
        rows = session.scalars(
            select(Conversation).where(Conversation.user_id == sid)
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "进行中"


# ---------- 轮转写入与重建 ----------


def test_roundtrip_with_tool_calls(store_tx):
    """完整工具调用轮转写读:四条消息类型/内容/tool_calls/tool_call_id 全还原。"""
    store = MySQLSessionStore()
    sid = _new_sid("store-roundtrip")

    store.append_message(sid, HumanMessage("订单1001"))
    store.append_message(
        sid,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_logistics",
                    "args": {"order_id": "ORD-1001"},
                    "id": "call_1",
                }
            ],
        ),
    )
    store.append_message(
        sid, ToolMessage("物流在中途", tool_call_id="call_1")
    )
    store.append_message(sid, AIMessage("已到广州"))

    history = store.get_history(sid)
    assert len(history) == 4

    user, ai_call, tool_msg, ai_final = history
    assert isinstance(user, HumanMessage)
    assert user.content == "订单1001"

    # 纯工具调用的 AIMessage:content 空串不丢,tool_calls 三键还原
    assert isinstance(ai_call, AIMessage)
    assert ai_call.content == ""
    assert ai_call.tool_calls == [
        {
            "name": "query_logistics",
            "args": {"order_id": "ORD-1001"},
            "id": "call_1",
            "type": "tool_call",
        }
    ]

    assert isinstance(tool_msg, ToolMessage)
    assert tool_msg.content == "物流在中途"
    assert tool_msg.tool_call_id == "call_1"

    assert isinstance(ai_final, AIMessage)
    assert ai_final.content == "已到广州"
    assert ai_final.tool_calls == []


def test_system_message_not_persisted(store_tx):
    """SystemMessage 不落库:连会话行都不建,消息表也无行。"""
    store = MySQLSessionStore()
    sid = _new_sid("store-sys")

    store.append_message(sid, SystemMessage("你是客服助手"))

    with Session(store_tx) as session:
        convs = session.scalars(
            select(Conversation).where(Conversation.user_id == sid)
        ).all()
        assert convs == []
        # 会话都没建,自然没有消息行
        msgs = session.scalars(
            select(Message).where(Message.conversation_id.in_(
                select(Conversation.id).where(Conversation.user_id == sid)
            ))
        ).all()
        assert msgs == []

    # 混入 Human 后再读回:历史里只有 Human,无 System
    store.append_message(sid, HumanMessage("你好"))
    history = store.get_history(sid)
    assert len(history) == 1
    assert isinstance(history[0], HumanMessage)
    assert history[0].content == "你好"


def test_two_rounds_keep_order(store_tx):
    """复合场景:两轮工具对话连续 append,读回顺序与写入一致。"""
    store = MySQLSessionStore()
    sid = _new_sid("store-2rounds")

    messages = [
        HumanMessage("第一轮:查订单 ORD-2001"),
        AIMessage(content="", tool_calls=[{
            "name": "query_order", "args": {"order_id": "ORD-2001"}, "id": "call_a",
        }]),
        ToolMessage("订单已发货", tool_call_id="call_a"),
        AIMessage("订单已发货,预计两天后到"),
        HumanMessage("第二轮:夹克多少钱"),
        AIMessage(content="", tool_calls=[{
            "name": "query_product", "args": {"product_name": "复古水洗牛仔夹克"},
            "id": "call_b",
        }]),
        ToolMessage("售价 299 元", tool_call_id="call_b"),
        AIMessage("复古水洗牛仔夹克售价 299 元"),
    ]
    for message in messages:
        store.append_message(sid, message)

    history = store.get_history(sid)
    assert len(history) == 8
    # 顺序与写入完全一致(按消息 id 升序)
    assert [type(m) for m in history] == [type(m) for m in messages]
    # 抽查两轮各自的锚点:tool_call_id 与最终回复一一对应
    assert history[2].tool_call_id == "call_a"
    assert history[6].tool_call_id == "call_b"
    assert history[3].content == "订单已发货,预计两天后到"
    assert history[7].content == "复古水洗牛仔夹克售价 299 元"


# ---------- get_or_create_conversation 语义 ----------


def test_get_or_create_idempotent(store_tx):
    """同一 session_id 调两次返回同一 id,不新建行。"""
    store = MySQLSessionStore()
    sid = _new_sid("store-idem")

    first = store.get_or_create_conversation(sid)
    second = store.get_or_create_conversation(sid)
    assert first == second

    with Session(store_tx) as session:
        rows = session.scalars(
            select(Conversation).where(Conversation.user_id == sid)
        ).all()
        assert len(rows) == 1


def test_get_or_create_reuses_existing_active_conversation(store_tx):
    """已有「进行中」会话时复用,不再新建(行数不变)。"""
    store = MySQLSessionStore()
    sid = _new_sid("store-reuse")

    # 预插一条「进行中」会话
    with Session(store_tx) as session:
        with session.begin():
            existing = Conversation(user_id=sid, status="进行中")
            session.add(existing)
            session.flush()
            existing_id = existing.id

    got = store.get_or_create_conversation(sid)
    assert got == existing_id

    with Session(store_tx) as session:
        rows = session.scalars(
            select(Conversation).where(Conversation.user_id == sid)
        ).all()
        assert len(rows) == 1  # 没有新建


def test_get_or_create_skips_closed_conversation(store_tx):
    """只有「已转人工」旧会话时:新建「进行中」会话,旧会话原样不动。"""
    store = MySQLSessionStore()
    sid = _new_sid("store-closed")

    with Session(store_tx) as session:
        with session.begin():
            old = Conversation(user_id=sid, status="已转人工")
            session.add(old)
            session.flush()
            old_id = old.id

    new_id = store.get_or_create_conversation(sid)
    assert new_id != old_id

    with Session(store_tx) as session:
        rows = session.scalars(
            select(Conversation)
            .where(Conversation.user_id == sid)
            .order_by(Conversation.id.asc())
        ).all()
        assert len(rows) == 2
        assert rows[0].status == "已转人工"  # 旧行不动
        assert rows[1].status == "进行中"
        assert rows[1].id == new_id


def test_get_history_skips_closed_conversation_messages(store_tx):
    """「已转人工」旧会话带消息:get_history 定位到新建会话,返回 [],
    旧会话的消息绝不混入(读路径验证,与 get_or_create 层互补)。"""
    store = MySQLSessionStore()
    sid = _new_sid("store-closed-read")

    # 预插一条「已转人工」会话,并带一条历史消息
    with Session(store_tx) as session:
        with session.begin():
            old = Conversation(user_id=sid, status="已转人工")
            session.add(old)
            session.flush()
            session.add(
                Message(
                    conversation_id=old.id,
                    role="user",
                    content="旧会话的遗留消息",
                )
            )

    assert store.get_history(sid) == []

    # 新会话写一条,读回也只有这一条——旧会话消息不串台
    store.append_message(sid, HumanMessage("新会话消息"))
    history = store.get_history(sid)
    assert len(history) == 1
    assert history[0].content == "新会话消息"


# ---------- Protocol 兼容 ----------


def test_mysql_session_store_satisfies_protocol():
    """MySQLSessionStore 满足 SessionStore Protocol。

    静态口径:显式继承 Protocol,类型检查器直接认可(下方赋值即静态断言)。
    运行时口径:SessionStore 未标 @runtime_checkable,不能用 isinstance,
    退而按结构化协议的本质——逐个断言两个 Protocol 方法真实存在且可调用。
    """
    store: SessionStore = MySQLSessionStore()
    assert callable(store.get_history)
    assert callable(store.append_message)
