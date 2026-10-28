"""Single global merge lease, bounded retries and resumable durable intake."""
import asyncio
from contextlib import asynccontextmanager
import logging
import time

from app.flywheel.locks import mysql_lock,LockUnavailable
from app.flywheel.normalization import NormalizedGap,MatchDecision,candidate_batches,validate_match,CandidateTooLong
from app.flywheel.store import PoolRepository
from app.observability.spans import execution_scope,trace_span

logger=logging.getLogger(__name__)
MERGE_LOCK='mewhelp:flywheel:merge'


async def _db(function,*args,**kwargs):
    # Cancellation must not release the advisory lease while its transaction still runs.
    operation=asyncio.create_task(asyncio.to_thread(function,*args,**kwargs))
    try: return await asyncio.shield(operation)
    except asyncio.CancelledError:
        try: await operation
        finally: raise


class FlywheelWorker:
    def __init__(self,normalizer,*,repository=None,clock=time.time,lock_factory=None,observability=None):
        self.normalizer=normalizer
        self.repository=repository or PoolRepository()
        self.clock=clock
        self.lock_factory=lock_factory or mysql_lock
        self.observability=observability
        self._wake=None;self._task=None;self._closing=False;self._unsubscribe=None

    @asynccontextmanager
    async def _lease(self):
        manager=self.lock_factory(MERGE_LOCK,timeout=0)
        acquisition=asyncio.create_task(asyncio.to_thread(manager.__enter__))
        try:
            lease=await asyncio.shield(acquisition)
        except asyncio.CancelledError:
            try:
                await acquisition
            except Exception:
                pass
            else:
                await asyncio.shield(asyncio.to_thread(manager.__exit__,None,None,None))
            raise
        try: yield lease
        finally: await asyncio.shield(asyncio.to_thread(manager.__exit__,None,None,None))

    async def _match(self,gap,candidates):
        async with asyncio.timeout(30):
            return validate_match(await self.normalizer.match(gap,candidates),candidates)

    async def _find(self,gap):
        after=0; winners=[]
        while True:
            rows=await asyncio.to_thread(self.repository.review_page,after,20)
            if not rows: break
            after=rows[-1]['id']
            # Keep all question/answer/condition text; reduce batch size rather than truncate.
            for batch in candidate_batches(rows):
                decision=await self._match(gap,batch)
                if decision.review_id is not None:
                    winners.append(next(row for row in batch if row['id']==decision.review_id))
        if not winners:
            return MatchDecision(review_id=None,reason='全状态全队列未发现同义项')
        best=winners[0]
        for candidate in winners[1:]:
            # Always compare the incumbent; splitting it away could silently discard it.
            groups=list(candidate_batches([best,candidate],max_tokens=11000))
            if len(groups)!=1: raise CandidateTooLong('finalist pair exceeds model input budget')
            decision=await self._match(gap,groups[0])
            if decision.review_id is None: raise ValueError('inconsistent finalist match')
            best=next(row for row in groups[0] if row['id']==decision.review_id)
        return MatchDecision(review_id=best['id'],reason='全队列比对后的最贴切同义项')

    async def run_once(self,limit=20):
        if type(limit) is not int or not 1<=limit<=20: raise ValueError('batch limit must be 1..20')
        result={'merged':0,'failed':0,'skipped':0}
        ids=await asyncio.to_thread(self.repository.pending_ids,limit,self.clock())
        for event_id in ids:
            try:
                async with self._lease() as lease:
                    row=await _db(self.repository.get_event,event_id)
                    if not self.repository.is_due(row,self.clock()):
                        result['skipped']+=1;continue
                    state=await _db(self.repository.begin_attempt,event_id,self.clock(),lease=lease)
                    if state is None or state['status']=='needs_retry':
                        result['skipped']+=1;continue
                    try:
                        event=row['event']
                        metadata={'event_id':event_id,'source':event.source}
                        if event.identity:
                            metadata.update(session_id=event.identity.session_id,source_trace_id=event.identity.trace_id)
                        with execution_scope('flywheel.process',self.observability,purpose='flywheel',metadata=metadata):
                            async with asyncio.timeout(30):
                                gap=NormalizedGap.model_validate(await self.normalizer.normalize(event))
                            match=await self._find(gap)
                            with trace_span('flywheel.merge',input={'event_id':event_id,'review_id':match.review_id}) as span:
                                rid=await _db(self.repository.merge_event,event_id,gap,match,lease=lease)
                                span.finish(output={'review_id':rid})
                        result['merged']+=1
                    except Exception as exc:
                        logger.warning('flywheel event failed id=%s exception=%s',event_id,type(exc).__name__)
                        await _db(self.repository.fail,event_id,type(exc).__name__,self.clock(),lease=lease)
                        result['failed']+=1
            except LockUnavailable:
                result['skipped']+=1
        return result

    def wake(self):
        if self._wake is not None: self._wake.set()

    async def _run(self):
        while not self._closing:
            self._wake.clear()
            try: await self.run_once()
            except Exception as exc: logger.warning('flywheel sweep failed exception=%s',type(exc).__name__)
            if self._closing: break
            try: await asyncio.wait_for(self._wake.wait(),timeout=30)
            except TimeoutError: pass

    async def start(self):
        if self._task is not None: return
        from app.flywheel.store import register_wakeup
        self._wake=asyncio.Event();self._closing=False
        loop=asyncio.get_running_loop()
        self._unsubscribe=register_wakeup(lambda:loop.call_soon_threadsafe(self.wake))
        self._task=asyncio.create_task(self._run())

    async def aclose(self):
        self._closing=True
        if self._unsubscribe: self._unsubscribe();self._unsubscribe=None
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            self._task=None
