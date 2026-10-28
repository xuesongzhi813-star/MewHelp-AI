"""Release coordination, durable publication and review decisions; no DDL."""
import asyncio
from contextlib import asynccontextmanager,contextmanager,aclosing
from contextvars import ContextVar
from functools import wraps
from dataclasses import asdict
from pathlib import Path
import re

from pydantic import BaseModel,ConfigDict,Field

from sqlalchemy import select,text

from app.db import get_engine,get_session_local
from app.flywheel.locks import mysql_lock
from app.flywheel.store import PoolRepository
from app.models import ReviewQueue
from app.observability.spans import traced_operation

OWNER_LOCK='mewhelp:ch09:owner'
PUBLISH_LOCK='mewhelp:ch09:publish'
MAINTENANCE_TEXT='知识库正在更新或校验，暂时无法回答知识问题，请稍后重试；订单等业务查询仍可使用。'
_owner_lease=None
_write_permission=ContextVar('ch09_release_write_permission',default=False)
_legacy_lease=ContextVar('ch09_legacy_writer',default=None)
_turn=ContextVar('ch09_knowledge_turn',default=None)


class KnowledgeMaintenance(RuntimeError):pass
class ReviewConflict(ValueError):pass


def owner_present():
    with get_engine().connect() as connection:
        return connection.scalar(text('SELECT IS_USED_LOCK(:name)'),{'name':OWNER_LOCK}) is not None


@contextmanager
def owner_scope():
    global _owner_lease
    with mysql_lock(OWNER_LOCK,timeout=0) as lease:
        _owner_lease=lease
        try:yield lease
        finally:_owner_lease=None


def guard_legacy_write():
    if _write_permission.get() and _owner_lease is not None:
        _owner_lease.assert_owned();return
    if _legacy_lease.get() is not None:
        _legacy_lease.get().assert_owned();return
    if owner_present():
        raise KnowledgeMaintenance('ch09 owner在线，请使用该应用审核/发布入口；旧知识写入已阻止。')


@contextmanager
def publication_write_scope():
    if _owner_lease is None:raise KnowledgeMaintenance('publication requires owner lease')
    _owner_lease.assert_owned()
    token=_write_permission.set(True)
    try:yield
    finally:_write_permission.reset(token)


@contextmanager
def legacy_write_scope():
    if _legacy_lease.get() is not None or _write_permission.get():
        guard_legacy_write();yield;return
    guard_legacy_write()
    with mysql_lock(OWNER_LOCK,timeout=0) as lease:
        token=_legacy_lease.set(lease)
        try:yield
        finally:_legacy_lease.reset(token)


def legacy_mutation(function):
    @wraps(function)
    def run(*args,**kwargs):
        with legacy_write_scope():return function(*args,**kwargs)
    return run


@asynccontextmanager
async def async_lock_scope(name,*,factory=mysql_lock):
    manager=factory(name,timeout=0)
    acquisition=asyncio.create_task(asyncio.to_thread(manager.__enter__))
    try:lease=await asyncio.shield(acquisition)
    except asyncio.CancelledError:
        try:await acquisition
        except Exception:pass
        else:await asyncio.shield(asyncio.to_thread(manager.__exit__,None,None,None))
        raise
    try:yield lease
    finally:await asyncio.shield(asyncio.to_thread(manager.__exit__,None,None,None))


@asynccontextmanager
async def async_legacy_write_scope():
    await asyncio.to_thread(guard_legacy_write)
    async with async_lock_scope(OWNER_LOCK) as lease:
        token=_legacy_lease.set(lease)
        try:yield
        finally:_legacy_lease.reset(token)


@asynccontextmanager
async def application_owner():
    global _owner_lease
    async with async_lock_scope(OWNER_LOCK) as lease:
        _owner_lease=lease
        try:yield lease
        finally:_owner_lease=None


class ReviewRepository(PoolRepository):
    def list(self,*,after=0,limit=50,status=None):
        statement=select(ReviewQueue).where(ReviewQueue.id>after).order_by(ReviewQueue.id).limit(limit)
        if status is not None:statement=statement.where(ReviewQueue.review_status==status)
        with self.factory() as session:return [self._review(row) for row in session.scalars(statement).all()]

    def detail(self,review_id):
        from app.models import LowConfidenceQuestion
        from app.flywheel.snapshots import read_snapshot
        with self.factory() as session:
            row=session.get(ReviewQueue,review_id)
            if row is None:raise LookupError('review not found')
            events=session.scalars(select(LowConfidenceQuestion).where(LowConfidenceQuestion.matched_review_id==review_id)
                .order_by(LowConfidenceQuestion.id)).all()
            return {**self._review(row),'events':[{'id':event.id,'raw_question':event.raw_question,
                'source':event.source,'reason':event.reason,'conversation_id':event.conversation_id,
                'snapshot':asdict(read_snapshot(event.retrieved_chunks))} for event in events]}

    def decide(self,review_id,status,*,approved_answer=None):
        if status not in {'通过','驳回'} or status=='通过' and not (approved_answer or '').strip():
            raise ValueError('human-approved answer required')
        answer=approved_answer.strip() if status=='通过' else None
        with self.factory() as session,session.begin():
            row=session.scalar(select(ReviewQueue).where(ReviewQueue.id==review_id).with_for_update())
            if row is None:raise LookupError('review not found')
            if row.review_status!='待审':
                if row.review_status==status and row.approved_answer==answer:return self._review(row)
                raise ReviewConflict('review decision is terminal')
            row.review_status=status;row.approved_answer=answer
            session.flush()
            return self._review(row)


class KnowledgeTurn:
    def __init__(self,coordinator):
        self.coordinator=coordinator;self.active=None;self.policy=None;self._lock=asyncio.Lock();self.borrowed=False

    async def ensure(self):
        async with self._lock:
            if self.active is None:
                prior=self.coordinator._read_context.get()
                self.borrowed=prior is not None
                self.active=prior if self.borrowed else await self.coordinator._acquire()
                try:
                    if self.coordinator.policy_loader:
                        self.policy=await asyncio.to_thread(self.coordinator.policy_loader,self.active)
                except asyncio.CancelledError:
                    if not self.borrowed:await self.coordinator._release()
                    self.active=None
                    raise
                except Exception:
                    if not self.borrowed:await self.coordinator._release()
                    self.active=None
                    self.coordinator.mark_writing()
                    raise KnowledgeMaintenance(MAINTENANCE_TEXT) from None
        return self.active

    async def close(self):
        if self.active is not None:
            if not self.borrowed:await self.coordinator._release()
            self.active=None


class ReleaseCoordinator:
    def __init__(self,*,active,validator=None,policy_loader=None,degraded=False):
        self.active=active;self.validator=validator;self.policy_loader=policy_loader
        self._condition=asyncio.Condition();self._writer=asyncio.Lock()
        self._readers=0;self._maintenance=False;self._degraded=degraded
        self._read_context=ContextVar('ch09_release_read_'+str(id(self)),default=None)

    async def _acquire(self):
        async with self._condition:
            if self._maintenance or self._degraded:raise KnowledgeMaintenance(MAINTENANCE_TEXT)
            try:value=self.active()
            except Exception:raise KnowledgeMaintenance(MAINTENANCE_TEXT) from None
            self._readers+=1
        try:
            if self.validator:await asyncio.to_thread(self.validator,value)
            return value
        except asyncio.CancelledError:
            await self._release()
            raise
        except Exception:
            await self._release();self._degraded=True
            raise KnowledgeMaintenance(MAINTENANCE_TEXT) from None

    async def _release(self):
        async with self._condition:
            self._readers-=1;self._condition.notify_all()

    @asynccontextmanager
    async def read_lease(self):
        prior=self._read_context.get()
        if prior is not None:
            yield prior;return
        value=await self._acquire();token=self._read_context.set(value)
        try:yield value
        finally:
            self._read_context.reset(token)
            await asyncio.shield(self._release())

    @asynccontextmanager
    async def maintenance(self):
        async with self._writer:
            try:
                async with self._condition:
                    self._maintenance=True
                    await self._condition.wait_for(lambda:self._readers==0)
                yield
            finally:
                async with self._condition:
                    self._maintenance=False;self._condition.notify_all()

    def mark_writing(self):self._degraded=True
    def mark_healthy(self):self._degraded=False

    @asynccontextmanager
    async def turn_scope(self):
        guard=KnowledgeTurn(self);token=_turn.set(guard)
        try:yield guard
        finally:
            try:await asyncio.shield(guard.close())
            finally:_turn.reset(token)

    async def ensure_read(self):
        guard=_turn.get()
        if guard is None or guard.coordinator is not self:raise RuntimeError('knowledge turn scope missing')
        return await guard.ensure()

    def turn_policy(self):
        guard=_turn.get()
        return guard.policy if guard is not None and guard.coordinator is self else None


def knowledge_turn_stream(function):
    @wraps(function)
    async def stream(owner,*args,**kwargs):
        runtime=getattr(owner,'runtime',owner)
        coordinator=getattr(runtime,'release_coordinator',None)
        if coordinator is None:
            async with aclosing(function(owner,*args,**kwargs)) as source:
                async for frame in source:yield frame
        else:
            async with coordinator.turn_scope():
                async with aclosing(function(owner,*args,**kwargs)) as source:
                    async for frame in source:yield frame
    return stream


def version_store():
    from app.config import get_settings
    from app.evaluation.versions import VersionStore
    return VersionStore(Path(get_settings().ch09_data_root))


def load_active(versions=None):
    from app.evaluation.contracts import ActiveRelease
    versions=versions or version_store()
    payload=versions.artifacts.read_json('active.json')
    return ActiveRelease(**payload['identity'])


class ReleaseManifest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    release_id: str
    stage: str
    base: dict
    confirmation: dict
    reviews: tuple[dict,...]
    dataset_version: str
    bindings: dict[str,int]=Field(default_factory=dict)
    write_started: bool=False
    profile_path: str | None=None
    profile_hash: str | None=None
    error_code: str | None=None


class Publisher:
    def __init__(self,versions,coordinator,impact,*,knowledge,calibrator,lock_factory=mysql_lock,
                 write_scope=publication_write_scope,fault=lambda stage:None,observability=None):
        self.observability=observability
        self.versions,self.coordinator,self.impact=versions,coordinator,impact
        self.knowledge,self.calibrator=knowledge,calibrator
        self.lock_factory,self.write_scope,self.fault=lock_factory,write_scope,fault

    def _path(self,release_id):
        if not re.fullmatch(r'release-[0-9a-f]{24}',release_id):raise ValueError('invalid release id')
        return f'releases/{release_id}/manifest.json'

    def get(self,release_id):
        result=ReleaseManifest.model_validate(self.versions.artifacts.read_json(self._path(release_id)))
        if result.release_id!=release_id:raise ValueError('release journal identity invalid')
        return result

    def _save(self,manifest):
        self.versions.artifacts.write_json(self._path(manifest.release_id),manifest.model_dump(mode='json'))

    def pending(self):
        root=self.versions.artifacts.root
        return [self.get(path.parent.name) for path in sorted(root.glob('releases/*/manifest.json'))
            if self.get(path.parent.name).stage!='complete']

    def list_releases(self,limit=20):
        paths=sorted(self.versions.artifacts.root.glob('releases/*/manifest.json'),key=lambda path:path.stat().st_mtime,reverse=True)
        items=[]
        for path in paths[:limit]:
            release=self.get(path.parent.name)
            items.append({'release_id':release.release_id,'stage':release.stage,'write_started':release.write_started,
                'error_code':release.error_code,'dataset_version':release.dataset_version,
                'review_ids':[review['id'] for review in release.reviews]})
        return items

    @traced_operation('knowledge.impact',purpose='label_impact')
    async def preview(self,review_ids):
        return await self.impact.prepare_impact(review_ids,base=load_active(self.versions))

    @asynccontextmanager
    async def _lock(self):
        from app.flywheel.worker import _db
        manager=self.lock_factory(PUBLISH_LOCK,timeout=0)
        acquired=asyncio.create_task(asyncio.to_thread(manager.__enter__))
        try:lease=await asyncio.shield(acquired)
        except asyncio.CancelledError:
            try:await acquired
            except Exception:pass
            else:await asyncio.shield(asyncio.to_thread(manager.__exit__,None,None,None))
            raise
        try:yield lease
        finally:await asyncio.shield(asyncio.to_thread(manager.__exit__,None,None,None))

    @traced_operation('knowledge.publish',purpose='publication')
    async def publish(self,confirmation):
        from app.evaluation.contracts import ConfirmedImpact,ImpactDraft
        from app.evaluation.artifacts import digest
        from app.flywheel.worker import _db
        if confirmation is None:raise ValueError('human confirmation required')
        confirmed=ConfirmedImpact.model_validate(confirmation)
        release_id='release-'+digest(confirmed.model_dump(mode='json'))[:24]
        async with self._lock() as lease:
            if self.versions.artifacts.exists(self._path(release_id)):
                return await self._resume(self.get(release_id),lease)
            if self.pending():raise ReviewConflict('unfinished release must be resumed first')
            draft=ImpactDraft.model_validate(self.versions.artifacts.read_json(f'impacts/{confirmed.digest}/draft.json'))
            self.impact.validate_current(draft)
            version=self.versions.freeze(draft.base['dataset_version'],draft,confirmed)
            await _db(self.knowledge.preflight)
            old=self.versions.artifacts.read_json('active.json')
            accumulated={review['id']:review for review in old.get('review_contents',[])}
            accumulated.update({review['id']:review for review in draft.reviews})
            manifest=ReleaseManifest(release_id=release_id,stage='prepared',base=draft.base,
                confirmation=confirmed.model_dump(mode='json'),reviews=tuple(accumulated[i] for i in sorted(accumulated)),
                dataset_version=version.version,bindings=old.get('review_bindings',{}))
            await _db(self._save,manifest)
            return await self._resume(manifest,lease)

    @traced_operation('knowledge.publish.retry',purpose='publication')
    async def retry(self,release_id):
        async with self._lock() as lease:return await self._resume(self.get(release_id),lease)

    @traced_operation('knowledge.calibrate',purpose='calibration')
    async def recalibrate(self):
        from app.evaluation.release import recalibrate_current
        async with self._lock() as lease:
            if self.pending():raise ReviewConflict('unfinished release must be resumed first')
            async with self.coordinator.maintenance():
                def assert_owned():
                    if _owner_lease is None:raise KnowledgeMaintenance('calibration requires owner lease')
                    _owner_lease.assert_owned();lease.assert_owned()
                result=await recalibrate_current(self.versions,self.calibrator,assert_owned=assert_owned)
                self.coordinator.mark_healthy()
                return result

    async def _resume(self,manifest,lease):
        from app.evaluation.artifacts import digest
        from app.evaluation.contracts import ActiveRelease
        from app.flywheel.worker import _db
        current=load_active(self.versions)
        if current.release_id==manifest.release_id:
            if current.profile_hash!=manifest.profile_hash or current.dataset_version!=manifest.dataset_version:
                raise ValueError('active/journal identity mismatch')
            if self.coordinator.validator:await _db(self.coordinator.validator,current)
            manifest=manifest.model_copy(update={'stage':'complete','error_code':None})
            await _db(self._save,manifest);self.coordinator.mark_healthy()
            return manifest
        if manifest.stage=='complete':return manifest
        if asdict(current)!=manifest.base:raise ReviewConflict('release baseline changed')
        # This remains valid during a failed write because active still points at the base.
        from app.evaluation.contracts import ImpactDraft
        draft=ImpactDraft.model_validate(self.versions.artifacts.read_json(f"impacts/{manifest.confirmation['digest']}/draft.json"))
        self.impact.validate_current(draft)
        version=self.versions.load(manifest.dataset_version)
        async with self.coordinator.maintenance():
            try:
                with self.write_scope():
                    manifest=manifest.model_copy(update={'stage':'writing','write_started':True,'error_code':None})
                    await _db(self._save,manifest)
                    self.coordinator.mark_writing()
                    lease.assert_owned()
                    new_bindings=await _db(self.knowledge.persist,draft.reviews,lease=lease)
                    self.fault('mysql_commit')
                    manifest=manifest.model_copy(update={'stage':'pending','bindings':{**manifest.bindings,**new_bindings}})
                    await _db(self._save,manifest);self.fault('pending')
                    ids=sorted(set(new_bindings.values()))
                    await _db(self.knowledge.vectorize,ids,boundary=self.fault)
                    manifest=manifest.model_copy(update={'stage':'done'})
                    await _db(self._save,manifest);lease.assert_owned()
                    profile=await self.calibrator(version,manifest.bindings,manifest.reviews)
                    profile_hash=digest(profile);profile_path=f'profiles/{profile_hash}.json'
                    await _db(self.versions.artifacts.immutable_json,profile_path,profile)
                    manifest=manifest.model_copy(update={'stage':'profile','profile_path':profile_path,'profile_hash':profile_hash})
                    await _db(self._save,manifest);self.fault('profile');lease.assert_owned()
                    # Re-enter the capability check after long calibration; owner may have disconnected.
                    with self.write_scope():pass
                    identity=ActiveRelease(manifest.release_id,version.version,version.labels_hash,version.split_hash,
                        profile['runtime_corpus_hash'],profile_hash)
                    await _db(self.versions.artifacts.write_json,'active.json',{'identity':asdict(identity),
                        'profile_path':profile_path,'review_bindings':manifest.bindings,'review_contents':manifest.reviews})
                    self.fault('active')
                    manifest=manifest.model_copy(update={'stage':'complete','error_code':None})
                    await _db(self._save,manifest);self.coordinator.mark_healthy()
            except Exception as exc:
                manifest=manifest.model_copy(update={'stage':'failed','error_code':type(exc).__name__})
                await _db(self._save,manifest)
        return manifest
