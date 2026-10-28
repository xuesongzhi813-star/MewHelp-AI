"""The existing MySQL problem pool retains immutable per-turn evidence."""
from dataclasses import asdict
import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.db import get_engine
from app.models import Conversation, Message, LowConfidenceQuestion


def test_shared_owner_lease_serializes_connection_checks_across_threads():
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    from app.flywheel.locks import LockLease
    class Connection:
        active=0;peak=0
        mutex=threading.Lock()
        def scalar(self,*args,**kwargs):
            with self.mutex:self.active+=1;self.peak=max(self.peak,self.active)
            try:time.sleep(.01);return 1
            finally:
                with self.mutex:self.active-=1
    connection=Connection();lease=LockLease(connection,'synthetic-owner')
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _:lease.assert_owned(),range(12)))
    assert connection.peak==1


@pytest.fixture
def pool_session():
    with get_engine().connect() as connection:
        outer = connection.begin()
        factory = sessionmaker(bind=connection, join_transaction_mode='create_savepoint')
        try:
            with factory() as session:
                yield session
        finally:
            outer.rollback()


def make_event(session, *, source='retrieval_low_conf'):
    from app.flywheel.contracts import TurnIdentity, EvidenceSnapshot, PoolEvent
    conversation = Conversation(user_id='ch09-pool-' + uuid.uuid4().hex)
    session.add(conversation)
    session.flush()
    question = Message(conversation_id=conversation.id, role='user', content='合成知识缺口')
    session.add(question)
    session.flush()
    identity = TurnIdentity('synthetic-session', conversation.id, question.id, 'a'*32, 'a'*32)
    snapshot = EvidenceSnapshot(identity, 'retrieved', [{'chunk_id':1,'answer':'当时原文','score':0.2}], {})
    return PoolEvent(identity, source, question.content, '证据不足', snapshot)


def test_same_turn_source_is_idempotent_and_snapshot_is_preserved(pool_session):
    from app.flywheel.store import record_event
    event = make_event(pool_session)
    first = record_event(event, session=pool_session)
    event.snapshot.hits[0]['answer'] = '不应覆盖初次快照'
    second = record_event(event, session=pool_session)
    assert second.id == first.id and first.created and not second.created
    row = pool_session.get(LowConfidenceQuestion, first.id)
    assert row.retrieved_chunks['hits'][0]['answer'] == '当时原文'
    assert row.retrieved_chunks['identity']['user_message_id'] == event.identity.user_message_id


def test_same_turn_different_source_keeps_both_events(pool_session):
    from app.flywheel.store import record_event
    from dataclasses import replace
    event = make_event(pool_session)
    first = record_event(event, session=pool_session)
    feedback = record_event(replace(event, source='user_feedback'), session=pool_session)
    assert first.id != feedback.id
    assert pool_session.get(LowConfidenceQuestion, feedback.id).source == 'user_feedback'


def test_snapshot_keeps_top_five_original_text_and_scores():
    from app.flywheel.contracts import TurnIdentity
    from app.flywheel.snapshots import snapshot_from_retrieval
    identity = TurnIdentity('s', 1, 2, 'a'*32, 'a'*32)
    hits = [{'chunk_id':i,'answer':f'原文{i}','score':i/10,'score_type':'rerank'} for i in range(8)]
    snapshot = snapshot_from_retrieval(identity, {'hits':hits})
    assert [h['chunk_id'] for h in snapshot.hits] == [0,1,2,3,4]
    assert snapshot.hits[2]['answer'] == '原文2' and snapshot.hits[2]['score'] == .2
    hits[2]['answer'] = '后续修改'
    assert snapshot.hits[2]['answer'] == '原文2'


def test_absent_retrieval_and_empty_retrieval_have_distinct_reasons():
    from app.flywheel.snapshots import snapshot_from_retrieval, read_snapshot
    assert snapshot_from_retrieval(None, None).snapshot_status == 'not_retrieved'
    assert snapshot_from_retrieval(None, {'hits':[]}).snapshot_status == 'no_hits'
    assert read_snapshot(None).snapshot_status == 'legacy_unknown'
    assert read_snapshot([{'answer':'旧数组'}]).hits[0]['answer'] == '旧数组'


def test_pool_rejects_invalid_source_without_writing(pool_session):
    from app.flywheel.store import record_event
    from dataclasses import replace
    event = make_event(pool_session)
    with pytest.raises(ValueError):
        record_event(replace(event, source='invented'), session=pool_session)


def test_named_lock_releases_connection_and_second_owner_can_acquire():
    from app.flywheel.locks import mysql_lock, LockUnavailable
    name = 'ch09-test-' + uuid.uuid4().hex
    with mysql_lock(name, timeout=0) as lease:
        lease.assert_owned()
        with pytest.raises(LockUnavailable):
            with mysql_lock(name, timeout=0):
                pass
    with mysql_lock(name, timeout=0) as lease:
        lease.assert_owned()


def test_legacy_callback_can_consume_structured_event_without_new_arguments(pool_session):
    from app.flywheel.store import event_scope
    from app.kb.low_confidence import record_low_confidence
    event = make_event(pool_session)
    with event_scope(event):
        first = record_low_confidence(event.raw_question, event.identity.conversation_id,
                                     event.source, event.reason, session=pool_session)
        second = record_low_confidence(event.raw_question, event.identity.conversation_id,
                                      event.source, event.reason, session=pool_session)
    assert first == second
    assert pool_session.get(LowConfidenceQuestion, first).retrieved_chunks['hits'][0]['answer'] == '当时原文'


def test_lost_lock_connection_is_not_treated_as_owned():
    from app.flywheel.locks import mysql_lock, LockUnavailable
    with mysql_lock('ch09-lost-' + uuid.uuid4().hex, timeout=0) as lease:
        lease.connection.invalidate()
        with pytest.raises(LockUnavailable):
            lease.assert_owned()
