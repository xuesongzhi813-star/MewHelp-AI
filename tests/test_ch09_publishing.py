import asyncio
from contextlib import contextmanager

import pytest

from app.evaluation.contracts import ActiveRelease


def active():return ActiveRelease('a','labels-'+'a'*20,'labels','split','corpus','profile')


async def test_maintenance_waits_existing_readers_rejects_new_and_is_reentrant():
    from app.flywheel.publishing import ReleaseCoordinator,KnowledgeMaintenance
    coordinator=ReleaseCoordinator(active=active)
    entered=asyncio.Event();release=asyncio.Event();writing=asyncio.Event()
    async def reader():
        async with coordinator.read_lease() as first:
            entered.set();await release.wait()
            async with coordinator.read_lease() as second:assert first==second
    async def writer():
        async with coordinator.maintenance():writing.set()
    reading=asyncio.create_task(reader());await entered.wait()
    pending=asyncio.create_task(writer());await asyncio.sleep(.01)
    assert not writing.is_set()
    with pytest.raises(KnowledgeMaintenance):
        async with coordinator.read_lease():pass
    release.set();await reading;await pending
    async with coordinator.read_lease() as value:assert value==active()


async def test_post_write_failure_stays_degraded_until_successful_retry():
    from app.flywheel.publishing import ReleaseCoordinator,KnowledgeMaintenance
    coordinator=ReleaseCoordinator(active=active)
    with pytest.raises(RuntimeError):
        async with coordinator.maintenance():
            coordinator.mark_writing()
            raise RuntimeError('synthetic crash')
    with pytest.raises(KnowledgeMaintenance):
        async with coordinator.read_lease():pass
    async with coordinator.maintenance():coordinator.mark_healthy()
    async with coordinator.read_lease():pass


async def test_failure_before_any_write_keeps_old_service():
    from app.flywheel.publishing import ReleaseCoordinator
    coordinator=ReleaseCoordinator(active=active)
    with pytest.raises(ValueError):
        async with coordinator.maintenance():raise ValueError('preflight')
    async with coordinator.read_lease() as value:assert value.release_id=='a'


def test_offline_cli_cannot_mutate_online_readers():
    from app.flywheel.publishing import owner_scope
    from app.flywheel.locks import mysql_lock,LockUnavailable
    with mysql_lock('mewhelp:ch09:owner',timeout=0):
        with pytest.raises(LockUnavailable):
            with owner_scope():pass


def test_job_dispatch_does_not_deadlock_parent_lock(monkeypatch):
    from app.flywheel.publishing import guard_legacy_write,KnowledgeMaintenance
    from app.core import jobs
    monkeypatch.setattr('app.flywheel.publishing.owner_present',lambda:True)
    launched=[]
    monkeypatch.setattr(jobs.subprocess,'Popen',lambda *a,**k:launched.append(a))
    with pytest.raises((KnowledgeMaintenance,jobs.JobAlreadyRunning)):
        jobs.start_job('kb-vectorize')
    assert not launched


async def test_cancelled_maintenance_wait_restores_old_service():
    from app.flywheel.publishing import ReleaseCoordinator
    coordinator=ReleaseCoordinator(active=active)
    async with coordinator.read_lease():
        async def maintain():
            async with coordinator.maintenance():pass
        task=asyncio.create_task(maintain());await asyncio.sleep(.01);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    async with coordinator.read_lease():pass


@pytest.mark.parametrize('boundary',['pending','flush','done','profile','active'])
async def test_publication_crash_boundaries_resume_without_duplicate_chunks(tmp_path,boundary):
    from contextlib import nullcontext
    from app.flywheel.publishing import Publisher,ReleaseCoordinator,load_active
    from tests.test_ch09_impact import setup,edit_for
    from app.evaluation.artifacts import digest
    store,version,old_active,_,impact,draft,_=await setup(tmp_path)
    edit=edit_for(draft)
    confirmed=impact.confirm_impact(draft.digest,edits=[edit],unchanged_case_ids=[c.id for c in version.cases if c.id!=edit['case_id']],confirmed_by='reviewer')
    store.artifacts.write_json('active.json',{'identity':draft.base,'review_bindings':{},'review_contents':[]})
    coordinator=ReleaseCoordinator(active=lambda:load_active(store))
    class Knowledge:
        inserted={};vectorized=[]
        def preflight(self):pass
        def persist(self,reviews,*,lease):
            from app.evaluation.versions import review_binding_key
            bindings={}
            for review in reviews:
                for chunk in review['chunks']:
                    key=review_binding_key(chunk['ref'])
                    self.inserted.setdefault(key,len(self.inserted)+500)
                    bindings[key]=self.inserted[key]
            return bindings
        def vectorize(self,ids,*,boundary):
            self.vectorized.append(list(ids));boundary('flush');boundary('done')
    knowledge=Knowledge()
    async def calibrator(version,bindings,reviews):
        return {'runtime_corpus_hash':'new-corpus','dataset_version':version.version,
            'labels_hash':version.labels_hash,'split_hash':version.split_hash,'policy':{'version':'synthetic'}}
    class Lease:
        def assert_owned(self):pass
    @contextmanager
    def lock(*args,**kwargs):yield Lease()
    failed=[False]
    def fault(stage):
        if stage==boundary and not failed[0]:failed[0]=True;raise RuntimeError('synthetic boundary crash')
    publisher=Publisher(store,coordinator,impact,knowledge=knowledge,calibrator=calibrator,
        lock_factory=lock,write_scope=nullcontext,fault=fault)
    first=await publisher.publish(confirmed)
    assert first.stage=='failed' and first.write_started
    assert load_active(store).release_id==(first.release_id if boundary=='active' else draft.base['release_id'])
    resumed=await publisher.retry(first.release_id)
    assert resumed.stage=='complete' and len(knowledge.inserted)==len(draft.reviews[0]['chunks'])
    assert load_active(store).release_id==resumed.release_id
    assert all(set(ids)<=set(knowledge.inserted.values()) for ids in knowledge.vectorized)
    again=await publisher.publish(confirmed)
    assert again.release_id==resumed.release_id and again.stage=='complete'


async def test_unconfirmed_publish_has_no_knowledge_side_effects(tmp_path):
    from app.flywheel.publishing import Publisher,ReleaseCoordinator
    from tests.test_ch09_impact import setup
    store,version,_,_,impact,_,_=await setup(tmp_path)
    class Forbidden:
        def preflight(self):raise AssertionError('unconfirmed call reached knowledge')
    publisher=Publisher(store,ReleaseCoordinator(active=active),impact,knowledge=Forbidden(),calibrator=None)
    with pytest.raises(ValueError):await publisher.publish(None)


async def test_maintenance_only_blocks_knowledge_without_collecting_gap(tmp_path):
    from app.flywheel.publishing import ReleaseCoordinator,MAINTENANCE_TEXT
    from app.workflow.runtime import WorkflowRuntime,open_checkpointer
    from app.history import InMemorySessionStore
    from tests.test_workflow_graph import Gateway
    coordinator=ReleaseCoordinator(active=active,degraded=True)
    collected=[];retrieved=[]
    async with open_checkpointer(str(tmp_path/'maintenance.sqlite')) as saver:
        knowledge=Gateway(intent='商品咨询')
        runtime=WorkflowRuntime(saver,InMemorySessionStore(10),gateway_factory=lambda _:knowledge,
            release_coordinator=coordinator,record=lambda *args:collected.append(args),retrieve=lambda *a,**k:retrieved.append(a))
        frames=[e async for e in runtime.stream_turn('knowledge','衣服怎么洗',model=knowledge)]
        assert ''.join(f.get('content','') for f in frames if f['type']=='token')==MAINTENANCE_TEXT
        assert not retrieved and not collected and knowledge.answers==0
        business=Gateway(intent='订单');runtime.gateway_factory=lambda _:business
        frames=[e async for e in runtime.stream_turn('business','查订单',model=business)]
        assert business.answers==1 and not any(f['type']=='error' for f in frames)
    assert coordinator._readers==0


def test_vectorize_ids_flushes_before_done_and_never_scans_pending_globally(monkeypatch):
    from app.kb import dualwrite
    from tests.test_kb_retriever import _stored
    from types import SimpleNamespace
    order=[]
    monkeypatch.setattr('app.flywheel.publishing.guard_legacy_write',lambda:None)
    monkeypatch.setattr('app.kb.migration.load_manifest',lambda:SimpleNamespace(target_collection='synthetic'))
    monkeypatch.setattr('app.kb.migration.eligible_chunk_ids',lambda:{1,2,99})
    from dataclasses import replace
    monkeypatch.setattr(dualwrite.store,'iter_chunks_by_ids',lambda ids:[replace(_stored(i),vectorize_status='pending') for i in ids])
    monkeypatch.setattr(dualwrite.store,'mark_done',lambda ids:order.append(('done',ids)))
    monkeypatch.setattr(dualwrite,'embed_texts',lambda texts:[[.1]]*len(texts))
    monkeypatch.setattr(dualwrite.milvus,'upsert_records',lambda rows,**kwargs:order.append(('upsert',[int(r['id']) for r in rows])))
    monkeypatch.setattr(dualwrite.milvus,'get_client',lambda:SimpleNamespace(flush=lambda name:order.append(('flush',name))))
    dualwrite.vectorize_ids([2])
    assert [x[0] for x in order]==['upsert','flush','done'] and order[0][1]==[2]
    assert order[-1][1]==[2]


async def test_closing_forwarded_confirmation_stream_releases_its_knowledge_lease(monkeypatch):
    from types import SimpleNamespace
    from app.flywheel.publishing import ReleaseCoordinator,knowledge_turn_stream
    from app.workflow.runtime import WorkflowRuntime
    coordinator=ReleaseCoordinator(active=active)
    runtime=SimpleNamespace(release_coordinator=coordinator)
    class Confirmation:
        def __init__(self,runtime):self.runtime=runtime
        @knowledge_turn_stream
        async def stream(self,request,*,model):
            await self.runtime.release_coordinator.ensure_read()
            yield {'type':'token','content':'synthetic'}
            await asyncio.Event().wait()
    monkeypatch.setattr('app.workflow.ticket_confirmation.TicketConfirmationCoordinator',Confirmation)
    source=WorkflowRuntime.stream_tool_confirmation(runtime,None,model=None)
    await anext(source);assert coordinator._readers==1
    await source.aclose()
    assert coordinator._readers==0


async def test_evaluation_outer_read_lease_allows_its_turns_while_publisher_waits():
    from app.flywheel.publishing import ReleaseCoordinator
    coordinator=ReleaseCoordinator(active=active)
    async def writing():
        async with coordinator.maintenance():pass
    async with coordinator.read_lease() as identity:
        task=asyncio.create_task(writing());await asyncio.sleep(.01)
        async with coordinator.turn_scope():
            assert await coordinator.ensure_read()==identity
    await task
