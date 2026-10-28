"""Current graph evaluation with explicit denominators and durable one-row runs."""
import asyncio
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
import uuid

from sqlalchemy import select

from app.db import get_session_local
from app.models import EvalRun
from app.evaluation.artifacts import digest
from app.flywheel.locks import mysql_lock
from app.flywheel.publishing import async_lock_scope
from app.flywheel.worker import _db
from app.kb.evaluation import retrieval_metrics,aggregate_results
from app.observability.spans import traced_operation

EVAL_LOCK='mewhelp:ch09:evaluation'
METRIC_PROTOCOL_VERSION='ch09-current-graph-v1'


def utcnow():return datetime.now(timezone.utc).isoformat()


def generation_code_identity():
    from pathlib import Path
    import hashlib
    root=Path(__file__).resolve().parents[2]
    return digest({str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in ('workflow','context') for path in sorted((root/'app'/folder).glob('*.py'))})


def comparison_key(metrics):
    return digest({key:metrics.get(key) for key in ('dataset_version','labels_hash','split_hash',
        'metric_protocol_version','judge_model','judge_prompt_hash')})


def build_trends(rows):
    previous={};points=[]
    for row in sorted(rows,key=lambda r:(r['metrics'].get('finished_at') or '',r['id'])):
        metrics=row['metrics'];key=comparison_key(metrics)
        scores=metrics.get('scores',{}) if metrics.get('status')=='success' else {}
        old=previous.get(key,{})
        values={name:scores.get(name) for name in ('recall_at_5','recall_at_10','mrr_at_10','faithfulness')}
        delta={name:values[name]-old[name] if values[name] is not None and old.get(name) is not None else None for name in values}
        points.append({'id':row['id'],'comparison_key':key,'status':metrics.get('status'),
            'finished_at':metrics.get('finished_at'),'values':values,'delta':delta,'identities':{
                k:metrics.get(k) for k in ('dataset_version','release_id','profile_hash','runtime_corpus_hash','generation_model')},
            'denominators':{k:v for k,v in metrics.get('scores',{}).items() if 'denominator' in k}})
        if scores:previous[key]=scores
    return points


def case_metrics(bound,retrieval,*,can_answer,faithfulness=None,error=None):
    case=bound.case
    ranks=[hit.chunk_id for hit in retrieval.hits] if retrieval is not None else []
    relevant=bound.relevant_ids if case.answerable and not error else set()
    at5=retrieval_metrics(ranks,relevant,k=5);at10=retrieval_metrics(ranks,relevant,k=10)
    candidates=retrieval_metrics(retrieval.candidate_ids if retrieval is not None else [],relevant,k=50)
    score=faithfulness.get('score') if isinstance(faithfulness,dict) and not faithfulness.get('error') else None
    judge_error=bool(can_answer and not error and isinstance(faithfulness,dict) and faithfulness.get('error'))
    return {'case_id':case.id,'bucket':case.bucket,'difficulty':case.difficulty,'strategy':'hybrid_rerank',
        'answerable':case.answerable,'can_answer':can_answer if not error else None,'service_error':error,
        'recall_at_5':at5['recall'],'recall_at_10':at10['recall'],'mrr_at_10':at10['mrr'],
        'candidate_recall_at_50':candidates['recall'],'all_required_hit':at10['all_required_hit'],
        'faithfulness':score if can_answer and not error else None,'judge_error':judge_error}


class EvalRepository:
    def __init__(self,*,session_factory=None):self.factory=session_factory or get_session_local()
    @staticmethod
    def _row(row):return {'id':row.id,'triggered_by':row.triggered_by,'dataset_size':row.dataset_size,'metrics':row.metrics}
    def list(self,limit=100):
        with self.factory() as session:
            return [self._row(row) for row in session.scalars(select(EvalRun).order_by(EvalRun.id.desc()).limit(limit)).all()]
    def _pages(self):
        after=0
        while True:
            with self.factory() as session:
                rows=[self._row(row) for row in session.scalars(select(EvalRun).where(EvalRun.id>after).order_by(EvalRun.id).limit(50)).all()]
            if not rows:break
            yield from rows;after=rows[-1]['id']
    def find_slot(self,slot):
        return next((row for row in self._pages() if row['metrics'].get('slot')==slot),None) if slot else None
    def recover_interrupted(self):
        for row in self._pages():
            if row['metrics'].get('status')=='running':
                self.finish(row['id'],{**row['metrics'],'status':'interrupted','finished_at':utcnow(),'error_code':'owner_run_lost'})
    def start(self,trigger,size,metrics,*,lease=None):
        with self.factory() as session,session.begin():
            row=EvalRun(triggered_by=trigger,dataset_size=size,metrics=metrics);session.add(row);session.flush()
            if lease is not None:lease.assert_owned()
            return row.id
    def finish(self,id,metrics):
        with self.factory() as session,session.begin():
            row=session.scalar(select(EvalRun).where(EvalRun.id==id).with_for_update())
            if row is None:raise LookupError('evaluation run missing')
            row.metrics=metrics


@dataclass(frozen=True)
class EvalRunResult:
    id:int
    status:str
    metrics:dict


class EvaluationRunner:
    def __init__(self,versions,coordinator,executor,*,repository=None,lock_factory=mysql_lock,binding_loader=None,observability=None):
        self.versions,self.coordinator,self.executor=versions,coordinator,executor
        self.repository=repository or EvalRepository();self.lock_factory=lock_factory
        self.binding_loader=binding_loader or self._bind
        self.observability=observability

    def _bind(self,version,identity):
        from app.evaluation.release import corpus_state
        from app.evaluation.versions import bind_version
        from app.kb.sources import get_sources
        payload=self.versions.artifacts.read_json('active.json')
        rows,_,vectors=corpus_state()
        return bind_version(version,get_sources(corpus_version='ch04'),rows,review_contents=payload.get('review_contents',[]),
            review_bindings=payload.get('review_bindings',{}),vector_ids=vectors)

    @traced_operation('evaluation.run',purpose='evaluation')
    async def run_evaluation(self,*,trigger,slot=None):
        if trigger not in {'手动','定时'} or slot is not None and trigger!='定时':raise ValueError('invalid evaluation trigger')
        async with async_lock_scope(EVAL_LOCK,factory=self.lock_factory) as lease:
            await _db(self.repository.recover_interrupted)
            existing=await _db(self.repository.find_slot,slot)
            if existing:return EvalRunResult(existing['id'],existing['metrics']['status'],existing['metrics'])
            metrics={'status':'running','run_id':uuid.uuid4().hex,'slot':slot,'started_at':utcnow(),
                'metric_protocol_version':METRIC_PROTOCOL_VERSION,
                'trigger_reason':'manual' if trigger=='手动' else 'scheduled_or_latest_catch_up'}
            id=await _db(self.repository.start,trigger,72,dict(metrics),lease=lease)
            rows=[]
            try:
                async with self.coordinator.read_lease() as identity:
                    version=self.versions.load(identity.dataset_version)
                    if (version.labels_hash,version.split_hash)!=(identity.labels_hash,identity.split_hash):
                        raise ValueError('active label identity mismatch')
                    from app.config import get_settings
                    from app.evaluation.executor import judge_prompt_identity
                    from app.kb.self_check import PROMPT as SUFFICIENCY_PROMPT
                    from app.workflow.agent import AGENT_PROMPT
                    metrics.update(asdict(identity),judge_model=get_settings().llm_model,
                        generation_model=get_settings().llm_model,judge_prompt_hash=judge_prompt_identity(),
                        generation_prompt_hash=digest(AGENT_PROMPT),self_check_prompt_hash=digest(SUFFICIENCY_PROMPT),
                        generation_code_hash=generation_code_identity())
                    await _db(self.repository.finish,id,dict(metrics))
                    bound=await _db(self.binding_loader,version,identity)
                    selected=[item for item in bound if item.case.split=='test']
                    if len(selected)!=72 or {item.case.id for item in selected}!=set(version.test_ids):
                        raise ValueError('exact 72 held-out cases required')
                    try:
                        if hasattr(self.executor,'start'):await self.executor.start()
                        for item in selected:
                            try:row=await self.executor(item,identity,metrics['run_id'])
                            except Exception as exc:row=case_metrics(item,None,can_answer=False,error=type(exc).__name__)
                            rows.append(row)
                    finally:
                        if hasattr(self.executor,'aclose'):await self.executor.aclose()
                    scores=aggregate_results(rows)['overall']['hybrid_rerank']
                    metrics.update(scores=scores,completed_cases=len(rows),failed_cases=sum(bool(r.get('service_error')) for r in rows))
                    invalid=self.coordinator.active()!=identity
                    if not invalid and self.coordinator.validator:
                        try:await _db(self.coordinator.validator,identity)
                        except Exception:invalid=True
                    metrics['status']='invalid' if invalid else 'failed' if metrics['failed_cases'] else 'success'
                    metrics['case_results']=[{k:v for k,v in row.items() if k not in {'answer','evidence'}} for row in rows]
                    lease.assert_owned()
            except asyncio.CancelledError:
                metrics.update(status='interrupted',finished_at=utcnow(),error_code='cancelled')
                await _db(self.repository.finish,id,metrics)
                raise
            except Exception as exc:metrics.update(status='failed',error_code=type(exc).__name__)
            metrics['finished_at']=utcnow()
            await _db(self.repository.finish,id,metrics)
            return EvalRunResult(id,metrics['status'],metrics)
