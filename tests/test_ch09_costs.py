from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

NOW=datetime(2026,10,27,12,tzinfo=timezone.utc)


@contextmanager
def lock(*args,**kwargs):yield SimpleNamespace(assert_owned=lambda:None)


def generation(id,trace,usage=None,cost=None):
    return {'id':id,'trace_id':trace,'type':'GENERATION','usage_details':usage,'total_cost':cost,'start_time':NOW,
        'metadata':{'intent':'','purpose':'chat'}}


def root(trace,intent='商品咨询',purpose='chat'):
    return {'id':'root-'+trace,'trace_id':trace,'type':'SPAN','is_root_observation':True,
        'parent_observation_id':None,'metadata':{'intent':intent,'purpose':purpose},'start_time':NOW,
        'usage_details':{'total':999999}}


class API:
    def __init__(self,generations,roots):
        self.pages=generations;self.roots=roots;self.calls=[];self.fail=False
        self.observations=self
    def get_many(self,**kwargs):
        self.calls.append(kwargs)
        if self.fail:raise ConnectionError('synthetic cloud outage')
        if kwargs.get('is_root_observation'):
            data=[row for row in self.roots if not kwargs.get('trace_id') or row['trace_id']==kwargs['trace_id']]
            return SimpleNamespace(data=data,meta=SimpleNamespace(cursor=None))
        index=int(kwargs.get('cursor') or '0')
        if isinstance(self.pages[index],Exception):raise self.pages[index]
        return SimpleNamespace(data=self.pages[index],meta=SimpleNamespace(cursor=str(index+1) if index+1<len(self.pages) else None))


def service(tmp_path,api,clock=lambda:NOW.timestamp()):
    from app.observability.costs import CostService
    return CostService(api,root=tmp_path,clock=clock,lock_factory=lock)


async def test_final_root_intent_join_deduplicates_pages_but_counts_real_retries(tmp_path):
    first=generation('first','t',{'input':60,'input_cache_read':40,'output':10,'total':110},.01)
    retry=generation('retry','t',{'input':20,'output':10,'total':30},.003)
    api=API([[first],[first,retry]],[root('t')])
    report=await service(tmp_path,api).collect_costs(NOW-timedelta(hours=1),NOW)
    assert report.complete and report.rows[0]['intent']=='商品咨询'
    row=report.rows[0]
    assert row['generation_count']==2 and row['request_count']==1 and row['total_tokens']==140
    assert row['input_tokens']==120 and row['output_tokens']==20 and row['cost_usd']==pytest.approx(.013)
    assert all('io' not in call.get('fields','') for call in api.calls)


async def test_pinned_sdk_observation_models_keep_real_trace_and_usage_fields(tmp_path):
    from langfuse.api.commons.types.observation_v2 import ObservationV2
    def sdk(row):
        return ObservationV2(project_id='synthetic-project',**row)
    child=sdk(generation('real-sdk-child','real-trace',{'input':40,'output':10,'total':50},.003))
    parent=sdk(root('real-trace'))
    # SDK JSON mode runs its own alias serializer; Python mode retains attribute names.
    assert child.model_dump(mode='json')['traceId']=='real-trace'
    assert child.model_dump()['trace_id']=='real-trace'
    report=await service(tmp_path,API([[child]],[parent])).collect_costs(NOW-timedelta(hours=1),NOW)
    assert report.complete and report.unassigned_generations==0
    assert len(report.rows)==1 and report.rows[0]['intent']=='商品咨询'
    assert report.rows[0]['total_tokens']==50 and report.rows[0]['cost_usd']==pytest.approx(.003)


async def test_missing_usage_and_price_are_unknown_and_purposes_are_separate(tmp_path):
    api=API([[generation('a','t'),generation('b','eval',{'total':500},.5)]],[root('t','退款退货'),root('eval','商品咨询','evaluation')])
    report=await service(tmp_path,api).collect_costs(NOW-timedelta(hours=1),NOW)
    assert len(report.rows)==1
    row=report.rows[0]
    assert row['intent']=='退款退货' and row['total_tokens'] is None and row['cost_usd'] is None
    assert row['unknown_usage_count']==1 and row['unknown_cost_count']==1


async def test_mid_pagination_failure_never_claims_complete(tmp_path):
    api=API([[generation('a','t',{'total':3},.01)],ConnectionError('next page')],[root('t')])
    result=await service(tmp_path,api).collect_costs(NOW-timedelta(hours=1),NOW)
    assert not result.complete and result.error_code and not result.cached


async def test_cache_survives_restart_and_outage_preserves_old_as_of(tmp_path):
    clock=[NOW.timestamp()]
    api=API([[generation('a','t',{'total':30},.01)]],[root('t')])
    first=await service(tmp_path,api,clock=lambda:clock[0]).collect_costs(NOW-timedelta(hours=1),NOW)
    calls=len(api.calls)
    second=await service(tmp_path,api,clock=lambda:clock[0]).collect_costs(NOW-timedelta(hours=1),NOW)
    assert second.cached and len(api.calls)==calls
    clock[0]+=901;api.fail=True
    failed=await service(tmp_path,api,clock=lambda:clock[0]).collect_costs(NOW-timedelta(hours=1),NOW)
    assert failed.stale and not failed.complete and failed.as_of==first.as_of
    assert failed.rows[0]['total_tokens']==30


async def test_oversized_or_older_than_hobby_retention_window_rejected(tmp_path):
    client=service(tmp_path,API([],[]))
    with pytest.raises(ValueError):await client.collect_costs(NOW-timedelta(days=31),NOW)
    with pytest.raises(ValueError):await client.collect_costs(NOW.replace(tzinfo=None),NOW)


def test_budget_persists_metrics_cap_and_minute_limit_across_restarts(tmp_path):
    from app.observability.costs import CloudReadBudget,ReadLimited
    clock=[NOW.timestamp()]
    for i in range(96):
        clock[0]=NOW.timestamp()+i*4
        CloudReadBudget(tmp_path,clock=lambda:clock[0],lock_factory=lock).reserve('metrics')
    with pytest.raises(ReadLimited):CloudReadBudget(tmp_path,clock=lambda:clock[0],lock_factory=lock).reserve('metrics')
    other=tmp_path/'minute'
    for i in range(20):CloudReadBudget(other,clock=lambda:clock[0],lock_factory=lock).reserve('observations')
    with pytest.raises(ReadLimited):CloudReadBudget(other,clock=lambda:clock[0],lock_factory=lock).reserve('observations')


async def test_429_retry_after_is_persisted_without_immediate_retry(tmp_path):
    from langfuse.api.core.api_error import ApiError
    api=API([[ApiError(status_code=429,headers={'Retry-After':'120'})]],[root('t')])
    api.pages=[ApiError(status_code=429,headers={'Retry-After':'120'})]
    first=await service(tmp_path,api).collect_costs(NOW-timedelta(hours=1),NOW)
    count=len(api.calls)
    second=await service(tmp_path,api).collect_costs(NOW-timedelta(hours=1),NOW)
    assert not first.complete and first.retry_at and not second.complete and len(api.calls)==count


async def test_nested_generation_aggregate_parent_is_not_counted_twice(tmp_path):
    parent=generation('parent','t',{'total':1000},1.)
    child={**generation('child','t',{'total':100},.1),'parent_observation_id':'parent'}
    report=await service(tmp_path,API([[parent,child]],[root('t')])).collect_costs(NOW-timedelta(hours=1),NOW)
    assert report.rows[0]['generation_count']==1 and report.rows[0]['total_tokens']==100


async def test_concurrent_refreshes_are_single_flight(tmp_path):
    import asyncio
    api=API([[generation('a','t',{'total':30},.1)]],[root('t')]);reader=service(tmp_path,api)
    a,b=await asyncio.gather(reader.collect_costs(NOW-timedelta(hours=1),NOW),reader.collect_costs(NOW-timedelta(hours=1),NOW))
    assert len(api.calls)==2 and a.complete and b.complete and b.cached


async def test_cost_api_only_exposes_status_and_aggregates(client,tmp_path):
    from app.main import app
    from app.api.observability import get_cost_service
    reader=service(tmp_path,API([[generation('a','t',{'total':30},.1)]],[root('t')]))
    app.dependency_overrides[get_cost_service]=lambda:reader
    response=await client.get('/api/observability/costs',params={'from':(NOW-timedelta(hours=1)).isoformat(),'to':NOW.isoformat()})
    assert response.status_code==200 and response.json()['rows'][0]['total_tokens']==30
    status=(await client.get('/api/observability/status')).json()
    assert status['dashboard_url']=='https://jp.cloud.langfuse.com'
    assert set(status)<={'status','enabled','keys_configured','region','dashboard_url','shutdown_confirmed'}
    assert (await client.get('/api/observability/costs',params={'from':'2020-01-01T00:00:00Z'})).status_code==422
