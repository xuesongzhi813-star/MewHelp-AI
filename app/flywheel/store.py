"""Durable intake with source/turn idempotency over the existing table."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import threading
import logging

from sqlalchemy import select
from app.db import get_session_local
from app.models import LowConfidenceQuestion
from app.flywheel.contracts import PoolEvent, RecordResult
from app.flywheel.locks import mysql_lock
from app.observability.spans import observed_call


SOURCES = {'retrieval_low_conf', 'self_check', 'user_feedback'}
_event: ContextVar[PoolEvent | None] = ContextVar('mewhelp_pool_event', default=None)
_listeners=set()
_listener_lock=threading.Lock()


def register_wakeup(callback):
    with _listener_lock: _listeners.add(callback)
    def unregister():
        with _listener_lock: _listeners.discard(callback)
    return unregister


def notify_committed():
    with _listener_lock: listeners=tuple(_listeners)
    for listener in listeners:
        try: listener()
        except Exception as exc:
            logging.getLogger(__name__).warning('flywheel wake failed exception=%s',type(exc).__name__)


def current_event():
    return _event.get()


@contextmanager
def event_scope(event: PoolEvent):
    token = _event.set(event)
    try:
        yield
    finally:
        _event.reset(token)


@observed_call('problem-pool-write', output_selector=lambda result: {'event_id':result.id,'created':result.created})
def record_event(event: PoolEvent, *, session=None, session_factory=None) -> RecordResult:
    if event.source not in SOURCES or not event.raw_question.strip():
        raise ValueError('invalid problem event')
    identity = event.identity
    if identity is not None and (identity.conversation_id <= 0 or identity.user_message_id <= 0):
        raise ValueError('invalid event identity')
    if event.snapshot.identity is not None and event.snapshot.identity != identity:
        raise ValueError('event snapshot identity mismatch')
    snapshot = asdict(event.snapshot)
    if identity is not None:
        snapshot['identity'] = asdict(identity)
    snapshot['processing'] = {**snapshot['processing'], 'status':'pending'}

    def insert(target, lease=None):
        if identity is not None:
            rows = target.scalars(select(LowConfidenceQuestion).where(
                LowConfidenceQuestion.conversation_id == identity.conversation_id,
                LowConfidenceQuestion.source == event.source).with_for_update()).all()
            for row in rows:
                old = row.retrieved_chunks
                previous = old.get('identity') if isinstance(old, dict) else None
                if (isinstance(previous, dict) and previous.get('user_message_id') == identity.user_message_id
                        and previous.get('session_id') == identity.session_id):
                    return RecordResult(row.id, False)
        row = LowConfidenceQuestion(raw_question=event.raw_question,
            conversation_id=identity.conversation_id if identity else event.conversation_id,
            source=event.source, reason=event.reason, retrieved_chunks=snapshot)
        target.add(row)
        target.flush()
        if lease is not None:
            lease.assert_owned()
        return RecordResult(row.id, True)

    if identity is None:
        if session is not None:
            return insert(session)
        factory = session_factory or get_session_local()
        with factory() as owned, owned.begin():
            result=insert(owned)
        notify_committed()
        return result
    name = 'mewhelp:pool:' + hashlib.sha256(
        f'{identity.conversation_id}:{identity.session_id}:{identity.user_message_id}:{event.source}'.encode()).hexdigest()[:44]
    with mysql_lock(name) as lease:
        if session is not None:
            return insert(session, lease)
        factory = session_factory or get_session_local()
        with factory() as owned, owned.begin():
            result=insert(owned, lease)
    notify_committed()
    return result


class PoolRepository:
    """Short transactions only; model calls happen after these methods return."""
    def __init__(self,*,session_factory=None):
        self.factory=session_factory or get_session_local()

    def get_event(self,event_id):
        from app.flywheel.snapshots import read_snapshot
        with self.factory() as session:
            row=session.get(LowConfidenceQuestion,event_id)
            if row is None:
                raise LookupError('problem event not found')
            snapshot=read_snapshot(row.retrieved_chunks)
            return {'id':row.id,'matched_review_id':row.matched_review_id,'processing':dict(snapshot.processing),
                'event':PoolEvent(snapshot.identity,row.source,row.raw_question,row.reason or '',snapshot,row.conversation_id)}

    @staticmethod
    def is_due(row,now):
        state=row['processing']
        return (row['matched_review_id'] is None and state.get('status')!='needs_retry'
            and state.get('next_retry_at',0)<=now)

    def pending_ids(self,limit=20,now=0):
        ids=[]; after=0
        while len(ids)<limit:
            with self.factory() as session:
                rows=session.scalars(select(LowConfidenceQuestion).where(LowConfidenceQuestion.id>after,
                    LowConfidenceQuestion.matched_review_id.is_(None)).order_by(LowConfidenceQuestion.id).limit(20)).all()
                if not rows: break
                for row in rows:
                    after=row.id
                    value=row.retrieved_chunks
                    processing=value.get('processing',{}) if isinstance(value,dict) else {}
                    if self.is_due({'matched_review_id':row.matched_review_id,'processing':processing},now):
                        ids.append(row.id)
                        if len(ids)==limit: break
        return ids

    def _processing(self,event_id,change,lease=None):
        from app.flywheel.snapshots import read_snapshot
        with self.factory() as session,session.begin():
            row=session.scalar(select(LowConfidenceQuestion).where(LowConfidenceQuestion.id==event_id).with_for_update())
            if row is None: raise LookupError('problem event not found')
            if row.matched_review_id is not None: return None
            value=asdict(read_snapshot(row.retrieved_chunks))
            state=change(dict(value['processing']))
            value['processing']=state
            row.retrieved_chunks=value
            session.flush()
            if lease is not None: lease.assert_owned()
            return state

    def begin_attempt(self,event_id,now,*,lease):
        def change(state):
            cycles=state.get('cycle_attempts',0)
            if cycles>=3:
                return {**state,'status':'needs_retry','next_retry_at':None}
            return {**state,'status':'processing','attempts':state.get('attempts',0)+1,
                'cycle_attempts':cycles+1,'next_retry_at':now+30,'worker_version':'flywheel-v1'}
        return self._processing(event_id,change,lease)

    def fail(self,event_id,code,now,*,lease):
        def change(state):
            cycles=state.get('cycle_attempts',1)
            history=[*state.get('error_history',[]),{'error_code':code,'at':now}][-20:]
            return {**state,'status':'needs_retry' if cycles>=3 else 'retry_wait',
                'error_code':code,'error_history':history,
                'next_retry_at':None if cycles>=3 else now+(30 if cycles==1 else 120)}
        return self._processing(event_id,change,lease)

    def retry(self,event_id):
        def change(state):
            return {**state,'status':'pending','cycle_attempts':0,'next_retry_at':0,
                'manual_retries':state.get('manual_retries',0)+1}
        with mysql_lock('mewhelp:flywheel:merge') as lease:
            result=self._processing(event_id,change,lease)
        notify_committed()
        return result

    @staticmethod
    def _review(row):
        return {key:getattr(row,key) for key in ('id','normalized_question','ai_suggested_answer',
            'occurrence_count','review_status','approved_answer')}

    def review_page(self,after=0,limit=20):
        from app.models import ReviewQueue
        with self.factory() as session:
            return [self._review(row) for row in session.scalars(select(ReviewQueue).where(ReviewQueue.id>after)
                .order_by(ReviewQueue.id).limit(limit)).all()]

    def get_review(self,review_id):
        from app.models import ReviewQueue
        with self.factory() as session:
            row=session.get(ReviewQueue,review_id)
            if row is None: raise LookupError('review not found')
            return self._review(row)

    def merge_event(self,event_id,gap,match,*,lease):
        from app.models import ReviewQueue
        from app.flywheel.snapshots import read_snapshot
        from app.flywheel.normalization import NORMALIZATION_VERSION,MATCH_VERSION
        with self.factory() as session,session.begin():
            lease.assert_owned()
            row=session.scalar(select(LowConfidenceQuestion).where(LowConfidenceQuestion.id==event_id).with_for_update())
            if row is None: raise LookupError('problem event not found')
            if row.matched_review_id is not None: return row.matched_review_id
            if match.review_id is None:
                review=ReviewQueue(normalized_question=gap.normalized_question,
                    ai_suggested_answer=gap.ai_suggested_answer,occurrence_count=1,review_status='待审')
                session.add(review);session.flush()
            else:
                review=session.scalar(select(ReviewQueue).where(ReviewQueue.id==match.review_id).with_for_update())
                if review is None: raise ValueError('matched review no longer exists')
                review.occurrence_count+=1
            value=asdict(read_snapshot(row.retrieved_chunks))
            value['processing']={**value['processing'],'status':'merged','next_retry_at':None,
                'normalization_version':NORMALIZATION_VERSION,'match_version':MATCH_VERSION,
                'match_reason':match.reason}
            row.retrieved_chunks=value
            row.matched_review_id=review.id
            session.flush();lease.assert_owned()
            return review.id


def merge_event(event_id,gap,match,*,lease):
    return PoolRepository().merge_event(event_id,gap,match,lease=lease)
