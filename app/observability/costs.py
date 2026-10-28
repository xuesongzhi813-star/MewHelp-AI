"""Final-root attribution over official Observations v2; unknown is never zero."""
import asyncio
from dataclasses import dataclass,asdict,replace
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
import math
from pathlib import Path
import time

from app.evaluation.artifacts import ArtifactStore,digest
from app.flywheel.locks import mysql_lock

INTENTS={'物流','订单','商品咨询','退款退货','售后','投诉','闲聊','其他'}
PURPOSES={'chat','summary','flywheel','evaluation','publication','calibration','label_impact','all'}


def iso(value):return datetime.fromtimestamp(value,timezone.utc).isoformat()


class ReadLimited(RuntimeError):
    def __init__(self,retry_at):self.retry_at=retry_at


class CloudReadBudget:
    def __init__(self,root,*,clock=time.time,lock_factory=mysql_lock):
        self.artifacts=ArtifactStore(root);self.clock=clock;self.lock_factory=lock_factory
    def _load(self):
        path='observability/cloud-budget.json'
        return self.artifacts.read_json(path) if self.artifacts.exists(path) else {
            'day':None,'metrics_count':0,'requests':[],'cooldown_until':0}
    def reserve(self,kind):
        if kind not in {'metrics','observations'}:raise ValueError('invalid Cloud read kind')
        with self.lock_factory('mewhelp:ch09:cloud-budget',timeout=0):
            state=self._load();now=self.clock();day=iso(now)[:10]
            if state['day']!=day:state.update(day=day,metrics_count=0)
            recent=[at for at in state.get('requests',[]) if at>now-60]
            if state.get('cooldown_until',0)>now:raise ReadLimited(state['cooldown_until'])
            if kind=='metrics' and state['metrics_count']>=96:
                tomorrow=datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
                raise ReadLimited(tomorrow.timestamp())
            if len(recent)>=20:raise ReadLimited(min(recent)+60)
            state['requests']=[*recent,now]
            if kind=='metrics':state['metrics_count']+=1
            self.artifacts.write_json('observability/cloud-budget.json',state)
    def defer(self,retry_at):
        with self.lock_factory('mewhelp:ch09:cloud-budget',timeout=0):
            state=self._load();state['cooldown_until']=max(state.get('cooldown_until',0),retry_at)
            self.artifacts.write_json('observability/cloud-budget.json',state)


@dataclass
class CostReport:
    start:str
    end:str
    purpose:str
    rows:list[dict]
    complete:bool
    as_of:str
    cached:bool=False
    stale:bool=False
    error_code:str | None=None
    retry_at:str | None=None
    unassigned_generations:int=0
    source:str='langfuse-observations-v2-root-join'
    currency:str='USD'


def _number(value,*,integer=False):
    if type(value) not in ((int,) if integer else (int,float)) or not math.isfinite(value) or value<0:return None
    return value


def _tokens(usage):
    if not isinstance(usage,dict):return None,None,None
    total=_number(usage.get('total'),integer=True)
    def part(prefix):
        values=[_number(v,integer=True) for k,v in usage.items()
                if (k==prefix or k.startswith(prefix+'_')) and not k.startswith(prefix+'_priority')]
        return sum(values) if values and all(v is not None for v in values) else None
    input,output=part('input'),part('output')
    if total is not None:
        # SDK5 splits exclusive input/output subtypes, but provider/manual metadata can differ.
        # The explicit total remains authoritative: never add cached tokens to it again.
        if output is not None and output<=total:input=total-output
        return input,output,total
    allowed=all(k in {'input','output'} or k.startswith(('input_','output_')) for k in usage)
    return input,output,input+output if allowed and input is not None and output is not None else None


def aggregate(generations,roots,*,purpose):
    groups={};seen=set();unassigned=0
    parent_generations={g.get('parent_observation_id') for g in generations if str(g.get('type','')).upper()=='GENERATION'}
    for generation in generations:
        if (str(generation.get('type','')).upper()!='GENERATION' or generation['id'] in seen
                or generation['id'] in parent_generations):continue
        seen.add(generation['id'])
        root=roots.get(generation.get('trace_id'))
        if root is None:
            unassigned+=1;continue
        metadata=root.get('metadata') or {}
        if purpose!='all' and metadata.get('purpose')!=purpose:continue
        intent=metadata.get('intent') if metadata.get('intent') in INTENTS else '未知意图'
        row=groups.setdefault(intent,{'intent':intent,'generation_count':0,'request_count':0,
            'input_tokens':0,'output_tokens':0,'total_tokens':0,'cost_usd':0.,
            'unknown_usage_count':0,'unknown_cost_count':0,'_traces':set(),'_known':[0,0,0,0]})
        row['generation_count']+=1;row['_traces'].add(generation.get('trace_id'))
        input,output,total=_tokens(generation.get('usage_details'))
        for i,(key,value) in enumerate(zip(('input_tokens','output_tokens','total_tokens'),(input,output,total))):
            if value is not None:row[key]+=value;row['_known'][i]+=1
        if total is None:row['unknown_usage_count']+=1
        cost=_number(generation.get('total_cost'))
        if cost is None:row['unknown_cost_count']+=1
        else:row['cost_usd']+=cost;row['_known'][3]+=1
    for row in groups.values():
        row['request_count']=len(row.pop('_traces'))
        known=row.pop('_known')
        for key,count in zip(('input_tokens','output_tokens','total_tokens','cost_usd'),known):
            if not count:row[key]=None
        row.update(usage_complete=row['unknown_usage_count']==0,cost_complete=row['unknown_cost_count']==0,
            usage_source='generation.usageDetails',cost_source='Langfuse observation.totalCost (USD)',
            pricing_date=None)
    return sorted(groups.values(),key=lambda r:-(r['total_tokens'] or 0)),unassigned


class CostService:
    def __init__(self,api,*,root=Path('data/local/ch09'),clock=time.time,lock_factory=mysql_lock):
        self.api=api;self.clock=clock;self.artifacts=ArtifactStore(root)
        self.budget=CloudReadBudget(root,clock=clock,lock_factory=lock_factory)
        self._flight=asyncio.Lock();self._default_end=None

    def default_window(self):
        now=self.clock()
        if self._default_end is None or now-self._default_end>=900:self._default_end=now
        end=datetime.fromtimestamp(self._default_end,timezone.utc)
        return end-timedelta(hours=24),end

    async def _page(self,**kwargs):
        try:await asyncio.to_thread(self.budget.reserve,'observations')
        except ReadLimited as exc:
            delay=exc.retry_at-self.clock()
            if not 0<delay<=60:raise
            await asyncio.sleep(delay)
            await asyncio.to_thread(self.budget.reserve,'observations')
        try:
            return await asyncio.to_thread(self.api.observations.get_many,**kwargs,
                request_options={'timeout_in_seconds':15,'max_retries':0})
        except Exception as exc:
            if getattr(exc,'status_code',None)==429:
                headers={str(k).lower():v for k,v in (getattr(exc,'headers',None) or {}).items()}
                delay=headers.get('retry-after','60')
                try:until=self.clock()+max(0.,float(delay))
                except (ValueError,TypeError):
                    try:until=parsedate_to_datetime(delay).timestamp()
                    except Exception:until=self.clock()+60
                await asyncio.to_thread(self.budget.defer,until)
                raise ReadLimited(until) from None
            raise

    async def _all(self,**kwargs):
        cursor=None;seen=set();rows=[]
        while True:
            response=await self._page(**kwargs,limit=1000,cursor=cursor)
            rows.extend(row.model_dump() if hasattr(row,'model_dump') else row for row in response.data)
            cursor=response.meta.cursor
            if not cursor:return rows
            if cursor in seen:raise ValueError('repeated observation cursor')
            seen.add(cursor)

    def _cached(self,path):
        if not self.artifacts.exists(path):return None
        try:return CostReport(**self.artifacts.read_json(path))
        except Exception:return None

    async def collect_costs(self,start,end,*,purpose='chat'):
        now=self.clock()
        if (start.tzinfo is None or end.tzinfo is None or start.utcoffset() is None or end.utcoffset() is None
                or start>=end or end-start>timedelta(days=30)
                or start.timestamp()<now-30*86400 or end.timestamp()>now+60 or purpose not in PURPOSES):
            raise ValueError('cost window unavailable; use aware timestamps within the last 30 days')
        start,end=start.astimezone(timezone.utc),end.astimezone(timezone.utc)
        key=digest({'start':start.isoformat(),'end':end.isoformat(),'purpose':purpose})
        path=f'observability/cost-cache/{key}.json'
        async with self._flight:
            now=self.clock()
            cached=await asyncio.to_thread(self._cached,path)
            if cached and now-datetime.fromisoformat(cached.as_of).timestamp()<900:
                return replace(cached,cached=True)
            rows=[];unassigned=0
            try:
                if self.api is None:raise RuntimeError('observability read client unavailable')
                generations=await self._all(type='GENERATION',fields='basic,usage,model',
                    from_start_time=start,to_start_time=end)
                roots_list=await self._all(is_root_observation=True,fields='basic,metadata,time',
                    expand_metadata='intent,purpose',from_start_time=start,to_start_time=end)
                roots={}
                for root in roots_list:
                    key=root.get('trace_id');prior=roots.get(key)
                    if prior is None or not root.get('parent_observation_id'):roots[key]=root
                for trace in {g.get('trace_id') for g in generations}-set(roots):
                    if trace:
                        recovered=await self._all(trace_id=trace,is_root_observation=True,fields='basic,metadata,time',
                            expand_metadata='intent,purpose')
                        if recovered:roots[trace]=next((r for r in recovered if not r.get('parent_observation_id')),recovered[0])
                rows,unassigned=aggregate(generations,roots,purpose=purpose)
                report=CostReport(start.isoformat(),end.isoformat(),purpose,rows,not bool(unassigned),iso(self.clock()),
                    unassigned_generations=unassigned,error_code='root_metadata_missing' if unassigned else None)
                if report.complete:await asyncio.to_thread(self.artifacts.write_json,path,asdict(report))
                return report
            except Exception as exc:
                retry=iso(exc.retry_at) if isinstance(exc,ReadLimited) else None
                if cached:return replace(cached,complete=False,cached=True,stale=True,error_code=type(exc).__name__,retry_at=retry)
                return CostReport(start.isoformat(),end.isoformat(),purpose,rows,False,iso(self.clock()),
                    error_code=type(exc).__name__,retry_at=retry)


_service=None


def set_cost_service(value):
    global _service
    _service=value


async def collect_costs(start,end,*,purpose='chat'):
    return await (_service or CostService(None)).collect_costs(start,end,purpose=purpose)
