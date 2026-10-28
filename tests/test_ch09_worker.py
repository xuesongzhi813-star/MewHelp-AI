from contextlib import contextmanager
from dataclasses import replace

import pytest

from tests.test_ch09_pool import pool_session, make_event


class Normalizer:
    def __init__(self, match_id=None, fail_first=False):
        self.match_id,self.fail_first=match_id,fail_first
        self.batches=[]

    async def normalize(self,event):
        from app.flywheel.normalization import NormalizedGap
        if self.fail_first:
            self.fail_first=False
            raise TimeoutError('synthetic failure')
        return NormalizedGap(normalized_question='衣服可以机洗吗？',ai_suggested_answer='AI建议：待确认材质后回答。')

    async def match(self,gap,candidates):
        from app.flywheel.normalization import MatchDecision
        self.batches.append(candidates)
        # Explicitly return invalid id too, to test the worker's independent validation.
        value=self.match_id if self.match_id in {c['id'] for c in candidates} or self.match_id==-1 else None
        return MatchDecision(review_id=value,reason='合成比对')


@pytest.fixture
def repo(pool_session):
    from sqlalchemy.orm import sessionmaker
    from app.flywheel.store import PoolRepository
    class IsolatedRepo(PoolRepository):
        event_ids=[]
        def pending_ids(self,limit=20,now=0):
            return [i for i in self.event_ids if self.is_due(self.get_event(i),now)][:limit]
    return IsolatedRepo(session_factory=sessionmaker(bind=pool_session.connection(),
        expire_on_commit=False,join_transaction_mode='create_savepoint'))


def seed(pool_session,repo,*,count=1):
    from app.flywheel.store import record_event
    event=make_event(pool_session)
    ids=[]
    for i in range(count):
        identity=replace(event.identity,user_message_id=event.identity.user_message_id+i)
        ids.append(record_event(replace(event,identity=identity,snapshot=replace(event.snapshot,identity=identity)),
            session=pool_session).id)
    repo.event_ids=ids
    return ids


def worker(repo,normalizer,*,clock=lambda:1000.,lock_factory=None):
    from app.flywheel.worker import FlywheelWorker
    return FlywheelWorker(normalizer,repository=repo,clock=clock,lock_factory=lock_factory)


async def test_new_gap_and_replay_increment_only_once(repo,pool_session):
    ids=seed(pool_session,repo)
    result=await worker(repo,Normalizer()).run_once()
    assert result['merged']==1
    event=repo.get_event(ids[0]); rid=event['matched_review_id']
    assert rid and repo.get_review(rid)['occurrence_count']==1
    assert (await worker(repo,Normalizer()).run_once())['merged']==0
    assert repo.get_review(rid)['occurrence_count']==1


async def test_scans_all_pages_and_all_statuses_rejected_is_terminal(repo,pool_session):
    from app.models import ReviewQueue
    rows=[ReviewQueue(normalized_question=f'候选{i}',review_status=['待审','通过','驳回'][i%3],
        occurrence_count=4) for i in range(43)]
    pool_session.add_all(rows);pool_session.flush()
    target=rows[-1]; target.review_status='驳回';pool_session.flush()
    ids=seed(pool_session,repo)
    normalizer=Normalizer(target.id)
    await worker(repo,normalizer).run_once()
    assert len(normalizer.batches)>=3
    assert {r['review_status'] for batch in normalizer.batches for r in batch}=={'待审','通过','驳回'}
    assert repo.get_event(ids[0])['matched_review_id']==target.id
    assert repo.get_review(target.id)['occurrence_count']==5
    assert repo.get_review(target.id)['review_status']=='驳回'


async def test_invalid_model_id_does_not_create_a_review(repo,pool_session):
    from app.models import ReviewQueue
    pool_session.add(ReviewQueue(normalized_question='已有问题'));pool_session.flush()
    ids=seed(pool_session,repo)
    before=len(repo.review_page(limit=10000))
    outcome=await worker(repo,Normalizer(-1)).run_once()
    assert outcome['failed']==1 and repo.get_event(ids[0])['matched_review_id'] is None
    assert len(repo.review_page(limit=10000))==before


async def test_failure_does_not_starve_next_event_and_backoff_persists(repo,pool_session):
    ids=seed(pool_session,repo,count=2)
    time=[1000.]
    normalizer=Normalizer(fail_first=True)
    outcome=await worker(repo,normalizer,clock=lambda:time[0]).run_once()
    assert outcome['failed']==1 and outcome['merged']==1
    event=repo.get_event(ids[0]); assert event['processing']['attempts']==1
    assert event['processing']['next_retry_at']==1030.
    assert (await worker(repo,Normalizer(),clock=lambda:time[0]).run_once())['merged']==0
    time[0]=1030.;normalizer.fail_first=True
    await worker(repo,normalizer,clock=lambda:time[0]).run_once()
    assert repo.get_event(ids[0])['processing']['next_retry_at']==1150.
    time[0]=1150.;normalizer.fail_first=True
    await worker(repo,normalizer,clock=lambda:time[0]).run_once()
    event=repo.get_event(ids[0]);assert event['processing']['status']=='needs_retry'
    assert event['processing']['attempts']==3
    repo.retry(ids[0])
    await worker(repo,Normalizer(),clock=lambda:time[0]).run_once()
    assert repo.get_event(ids[0])['processing']['attempts']==4
    assert repo.get_event(ids[0])['matched_review_id'] is not None


async def test_lost_lease_prevents_atomic_merge(repo,pool_session):
    from app.flywheel.locks import LockUnavailable
    ids=seed(pool_session,repo)
    before=len(repo.review_page(limit=10000))
    class Lease:
        def assert_owned(self): raise LockUnavailable('lost')
    @contextmanager
    def lock(*args,**kwargs): yield Lease()
    await worker(repo,Normalizer(),lock_factory=lock).run_once()
    assert repo.get_event(ids[0])['matched_review_id'] is None
    assert len(repo.review_page(limit=10000))==before


async def test_commit_boundary_crash_replay_keeps_count(repo,pool_session):
    from app.flywheel.normalization import NormalizedGap,MatchDecision
    from app.flywheel.locks import mysql_lock
    ids=seed(pool_session,repo)
    with mysql_lock('mewhelp:flywheel:merge',timeout=0) as lease:
        gap=NormalizedGap(normalized_question='标准问题',ai_suggested_answer='待确认')
        rid=repo.merge_event(ids[0],gap,MatchDecision(review_id=None,reason='新缺口'),lease=lease)
        assert repo.merge_event(ids[0],gap,MatchDecision(review_id=None,reason='重跑'),lease=lease)==rid
    assert repo.get_review(rid)['occurrence_count']==1


def test_candidate_batches_reduce_size_without_truncating_meaning():
    from app.flywheel.normalization import candidate_batches,CandidateTooLong
    candidates=[{'id':i,'normalized_question':'衣服能机洗吗？'*40} for i in range(24)]
    batches=list(candidate_batches(candidates,max_tokens=1800))
    assert len(batches)>2 and [row for batch in batches for row in batch]==candidates
    with pytest.raises(CandidateTooLong):
        list(candidate_batches([{'id':1,'normalized_question':'长'*10000}],max_tokens=100))


async def test_normalizer_bad_output_fails_without_silently_creating_gap():
    from app.flywheel.normalization import ModelNormalizer,NormalizedGap
    class Gateway:
        async def normalize_gap(self,messages,budget):return {'normalized_question':'x','ai_suggested_answer':'y','extra':True}
    from app.flywheel.contracts import PoolEvent,EvidenceSnapshot
    with pytest.raises(ValueError):
        await ModelNormalizer(Gateway()).normalize(PoolEvent(None,'self_check','原话','原因',EvidenceSnapshot(None,'legacy_unknown',[])))


async def test_tied_matches_use_stable_smallest_id():
    from app.flywheel.normalization import NormalizedGap,MatchDecision
    class Repo:
        def review_page(self,after=0,limit=20):
            return [{'id':i,'normalized_question':'同义'} for i in range(after+1,min(after+limit+1,45))]
    class Tied:
        async def match(self,gap,candidates):
            ids=[c['id'] for c in candidates]
            return MatchDecision(review_id=ids[-1],tied_ids=ids,reason='都同等')
    decision=await worker(Repo(),Tied())._find(NormalizedGap(normalized_question='同义',ai_suggested_answer='待核'))
    assert decision.review_id==1


async def test_worker_wakeup_and_close_unregister_listener():
    import asyncio
    from app.flywheel.store import notify_committed
    value=worker(None,Normalizer())
    called=asyncio.Event()
    async def run_once(limit=20): called.set();return {}
    value.run_once=run_once
    await value.start();await asyncio.wait_for(called.wait(),1)
    called.clear()
    notify_committed()
    await asyncio.wait_for(called.wait(),1)
    await value.aclose()
    assert value._task is None and value._unsubscribe is None


async def test_cancel_during_lock_acquisition_still_releases_it():
    import asyncio
    import threading
    entered=threading.Event();allow=threading.Event();released=threading.Event()
    @contextmanager
    def delayed(*args,**kwargs):
        entered.set();allow.wait(2)
        try: yield object()
        finally: released.set()
    value=worker(None,Normalizer(),lock_factory=delayed)
    async def hold():
        async with value._lease(): await asyncio.sleep(10)
    task=asyncio.create_task(hold())
    await asyncio.to_thread(entered.wait,1)
    task.cancel();allow.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert released.wait(.2)


async def test_two_consumers_cannot_create_duplicate_gap(repo,pool_session):
    import asyncio
    from app.flywheel.normalization import MatchDecision
    ids=seed(pool_session,repo,count=2)
    started=asyncio.Event();release=asyncio.Event()
    class Matching(Normalizer):
        async def normalize(self,event):
            started.set();await release.wait()
            return await super().normalize(event)
        async def match(self,gap,candidates):
            return MatchDecision(review_id=candidates[0]['id'],reason='同义合成项')
    # Existing production review rows must not become synthetic match targets.
    original=repo.review_page
    floor=max([r['id'] for r in original(limit=10000)] or [0])
    repo.review_page=lambda after=0,limit=20: original(max(after,floor),limit)
    first=worker(repo,Matching())
    pending=asyncio.create_task(first.run_once())
    await asyncio.wait_for(started.wait(),2)
    second=await worker(repo,Matching()).run_once()
    assert second['merged']==0
    release.set();assert (await pending)['merged']==2
    a,b=[repo.get_event(i)['matched_review_id'] for i in ids]
    assert a==b and repo.get_review(a)['occurrence_count']==2


async def test_cancelled_transaction_finishes_before_lease_release():
    import asyncio
    import threading
    from app.flywheel.worker import _db
    started=threading.Event();finish=threading.Event();released=threading.Event()
    @contextmanager
    def lock(*args,**kwargs):
        try: yield object()
        finally: released.set()
    def commit():started.set();finish.wait(2)
    async def process():
        async with worker(None,Normalizer(),lock_factory=lock)._lease():await _db(commit)
    task=asyncio.create_task(process())
    await asyncio.to_thread(started.wait,1)
    task.cancel()
    await asyncio.sleep(.02)
    assert not released.is_set()
    finish.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert released.is_set()
