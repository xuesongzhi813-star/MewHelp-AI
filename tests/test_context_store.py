import uuid

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_engine
from app.models import Conversation, Message
from app.context.store import ConversationStore


@pytest.fixture
def store():
    with get_engine().connect() as conn:
        outer = conn.begin()
        factory = sessionmaker(bind=conn, join_transaction_mode='create_savepoint')
        try:
            yield ConversationStore(factory), factory
        finally:
            outer.rollback()


def test_summary_cas_gaps_monotonic_and_rollback(store):
    db, factory = store
    ident = db.create_conversation(uuid.uuid4().hex)
    other = db.create_conversation(uuid.uuid4().hex)
    a = db.append_user(ident.conversation_id, '订单ORD-1001未收到')
    db.append_user(other.conversation_id, '制造id间隙')
    b = db.append_final(ident.conversation_id, a.id, '帮您查物流')
    c = db.append_user(ident.conversation_id, '谢谢')
    db.advance_layer1(ident.conversation_id, c.id)
    db.advance_layer1(ident.conversation_id, a.id)
    row = db.append_summary(ident.conversation_id, expected_upto=None,
        from_msg_id=a.id, upto_msg_id=b.id, content='订单ORD-1001未收到，需查物流。')
    assert row.seq == 1
    assert db.append_summary(ident.conversation_id, expected_upto=None,
        from_msg_id=a.id, upto_msg_id=b.id, content='重复') is None
    snapshot = db.read_context(ident.conversation_id)
    assert snapshot.summary_upto_msg_id == b.id
    assert snapshot.layer1_from_msg_id == c.id
    assert [r.id for r in db.read_messages(ident.conversation_id, from_id=a.id, upto_id=b.id)] == [a.id, b.id]
    with factory() as s:
        assert s.get(Conversation, ident.conversation_id).summary is None
    with pytest.raises(ValueError):
        db.append_summary(ident.conversation_id, expected_upto=b.id,
            from_msg_id=c.id, upto_msg_id=c.id, content='不能覆盖层1')
    assert len(db.read_context(ident.conversation_id).summaries) == 1


def test_recover_final_slot_after_db_commit_before_checkpoint(store):
    db, _ = store
    cid = db.create_conversation(uuid.uuid4().hex).conversation_id
    first = db.append_user(cid, '同样问题')
    final = db.append_final(cid, first.id, '同样答复')
    assert db.append_final(cid, first.id, '同样答复').id == final.id
    with pytest.raises(ValueError, match='final'):
        db.append_final(cid, first.id, '不同答复')
    second = db.append_user(cid, '同样问题')
    assert db.append_final(cid, second.id, '同样答复').id != final.id
    assert db.read_messages(cid)[-1].turn_from_msg_id == second.id


def test_new_archive_skips_tool_and_pure_tool_call(store):
    db, _ = store
    cid = db.create_conversation(uuid.uuid4().hex).conversation_id
    user = db.append_user(cid, '查物流')
    assert db.archive_final(cid, user.id, ToolMessage('巨大工具结果', tool_call_id='x')) is None
    assert db.archive_final(cid, user.id, AIMessage('', tool_calls=[dict(name='query_order', args={}, id='x')])) is None
    assert len(db.read_messages(cid)) == 1


def test_owned_reads_and_list_do_not_create_and_ignore_status(store):
    db, factory = store
    user = uuid.uuid4().hex
    a, b = db.create_conversation(user), db.create_conversation(user)
    db.append_user(a.conversation_id, '首问'*60)
    with factory.begin() as s:
        s.get(Conversation, a.conversation_id).status = '已转人工'
    assert db.get_owned(a.conversation_id, user) == a
    with pytest.raises(LookupError):
        db.get_owned(a.conversation_id, uuid.uuid4().hex)
    with pytest.raises(LookupError):
        db.get_owned(9223372036854775000, user)
    rows = db.list_conversations(user)
    assert [r['id'] for r in rows] == [b.conversation_id, a.conversation_id]
    assert len(rows[1]['preview']) == 80 and not rows[1]['has_summary']


def test_summary_insert_rolls_back_if_anchor_update_fails(store, monkeypatch):
    from sqlalchemy import event
    db, factory = store
    cid = db.create_conversation(uuid.uuid4().hex).conversation_id
    a = db.append_user(cid, 'a')
    b = db.append_final(cid, a.id, 'b')
    c = db.append_user(cid, 'c')
    db.advance_layer1(cid, c.id)
    def fail(mapper, connection, target):
        if target.summary_upto_msg_id is not None:
            raise RuntimeError('injected failure')
    event.listen(Conversation, 'before_update', fail)
    try:
        with pytest.raises(RuntimeError, match='injected'):
            db.append_summary(cid, expected_upto=None, from_msg_id=a.id, upto_msg_id=b.id, content='a/b')
    finally:
        event.remove(Conversation, 'before_update', fail)
    assert db.read_context(cid).summaries == []
    assert db.read_context(cid).summary_upto_msg_id is None
