"""Real local acceptance, retaining only this run's synthetic records and evidence."""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.integrate_ch08 import events


def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=lambda v:v.isoformat()),encoding='utf-8')


def pool_rows(conversation_ids):
    from app.db import get_session_local
    from app.models import LowConfidenceQuestion
    from sqlalchemy import select
    with get_session_local()() as session:
        rows=session.scalars(select(LowConfidenceQuestion).where(
            LowConfidenceQuestion.conversation_id.in_(conversation_ids)).order_by(LowConfidenceQuestion.id)).all()
        return [dict(id=r.id,source=r.source,raw_question=r.raw_question,
            matched_review_id=r.matched_review_id,snapshot=r.retrieved_chunks) for r in rows]


async def merge(ids):
    from app.flywheel.store import PoolRepository
    from app.flywheel.worker import FlywheelWorker
    from app.flywheel.normalization import ModelNormalizer
    from app.workflow.models import ModelGateway
    from app.llm import build_workflow_model
    from app.config import get_settings
    from app.observability.runtime import ObservabilityRuntime
    class ScopedRepository(PoolRepository):
        def pending_ids(self,limit=20,now=0):
            return [i for i in ids if self.is_due(self.get_event(i),now)][:limit]
    observed=ObservabilityRuntime(get_settings())
    await asyncio.to_thread(observed.start)
    try:
        worker=FlywheelWorker(ModelNormalizer(ModelGateway(build_workflow_model(get_settings()))),
            repository=ScopedRepository(),observability=observed)
        return await worker.run_once()
    finally:await asyncio.to_thread(observed.close)


async def audit_traces(report,path):
    from app.config import get_settings
    from app.observability.runtime import ObservabilityRuntime
    from app.observability.costs import CloudReadBudget
    from scripts.test_ch09 import redact,configured_secrets
    observed=ObservabilityRuntime(get_settings())
    await asyncio.to_thread(observed.start)
    budget=CloudReadBudget(Path(get_settings().ch09_data_root))
    result={}
    try:
        if observed.status!='ready':raise RuntimeError('Cloud not ready')
        for name,row in report['turns'].items():
            trace_id=row['turn']['trace_id'];cursor=None;items=[]
            while True:
                await asyncio.to_thread(budget.reserve,'observations')
                response=await asyncio.to_thread(observed.read_api.observations.get_many,
                    trace_id=trace_id,fields='core,basic,time,io,metadata,model,usage,prompt',limit=1000,cursor=cursor)
                items.extend(item.model_dump() for item in response.data)
                cursor=response.meta.cursor
                if not cursor:break
            raw=path.with_name('trace-'+name+'.json')
            secrets=configured_secrets()
            def scrub(value):
                if isinstance(value,str):return redact(value,secrets)
                if isinstance(value,list):return [scrub(v) for v in value]
                if isinstance(value,dict):return {k:scrub(v) for k,v in value.items()}
                return value
            save(raw,scrub(items))
            ids={item['id'] for item in items}
            result[name]={'trace_id':trace_id,'observation_count':len(items),
                'missing_parents':[item['parent_observation_id'] for item in items if not item.get('is_root_observation') and item.get('parent_observation_id') and item['parent_observation_id'] not in ids],
                'observations':[{k:item.get(k) for k in ['id','name','type','parent_observation_id','is_root_observation','start_time','end_time','usage_details','total_cost','level','session_id']}
                    |{'has_input':item.get('input') is not None,'has_output':item.get('output') is not None,
                      'intent':(item.get('metadata') or {}).get('intent'),'purpose':(item.get('metadata') or {}).get('purpose')}
                    for item in items]}
            assert items,'Cloud returned no observations'
        return result
    finally:await asyncio.to_thread(observed.close)


async def run(args):
    import httpx
    path=ROOT/'log/ch09/acceptance'/args.run_id/'evidence.json'
    report=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'run_id':args.run_id,'user_id':uuid.uuid4().hex,'turns':{}}
    def checkpoint():save(path,report)
    async with httpx.AsyncClient(base_url=args.base_url,timeout=900,trust_env=False) as client:
        async def request(method,url,**kw):
            response=await client.request(method,url,**kw)
            response.raise_for_status()
            return response.json()
        async def chat(name,question,identity=None):
            body={**(identity or {'session_id':args.run_id+'-'+name,'user_id':report['user_id']}),'message':question}
            frames=await events(client,'/chat',body)
            meta=next((f for f in frames if f['type']=='conversation'),identity)
            identity={k:meta[k] for k in ('session_id','user_id','conversation_id')}
            turns=[f for f in frames if f['type']=='turn']
            row={'question':question,'identity':identity,'frames':frames,'turn':turns[-1] if turns else None,
                'answer':''.join(f.get('content','') for f in frames if f['type']=='token')}
            report['turns'][name]=row;checkpoint()
            print(json.dumps({'turn':name,'frames':[f['type'] for f in frames if f['type']!='token'],
                'answer_length':len(row['answer']),'stable_turn':bool(row['turn'])}),flush=True)
            return row
        report['status']=await request('GET','/api/observability/status');checkpoint()
        if args.phase=='probe':
            await chat('known','店铺满多少元包邮，偏远地区怎么算运费？')
            question=f'验收专用商品 CH09-{args.run_id} 演示布袋的防伪查询口令是什么？'
            report['gap_question']=question
            gap=await chat('gap',question)
            await chat('later','你好，谢谢你。',gap['identity'])
            for name in ['gap','later','known']:
                row=report['turns'][name];turn=row['turn']
                if not turn or not turn.get('assistant_message_id'):raise AssertionError('missing stable archived turn')
                body={key:turn[key] for key in ['session_id','user_message_id','assistant_message_id']};body['rating']='down'
                report.setdefault('feedback',{})[name]=await request('POST','/api/feedback',json=body)
                report['feedback'][name+'_repeat']=await request('POST','/api/feedback',json=body)
                assert report['feedback'][name]['event_id']==report['feedback'][name+'_repeat']['event_id']
                checkpoint()
        if args.phase=='self-check':
            await chat('partial',args.self_check_question)
        cids=[row['identity']['conversation_id'] for row in report['turns'].values()]
        report['pool']=pool_rows(cids);checkpoint()
        if args.phase=='probe':
            assert report['turns']['known']['answer'] and '正在更新或校验' not in report['turns']['known']['answer'],'knowledge maintenance is not successful acceptance'
            assert any(f['type']=='citations' for f in report['turns']['known']['frames']),'known question did not reach grounded answer'
            assert any(r['raw_question']==report['gap_question'] and r['source'] in {'retrieval_low_conf','self_check'} for r in report['pool']),'gap did not pass intake gate'
            assert report['feedback']['gap']['snapshot_status'] not in {'not_retrieved','snapshot_unavailable'},'old retrieved turn snapshot was not recovered'
        if args.phase=='self-check':
            assert any(r['source']=='self_check' for r in report['pool']),'real model did not exercise self-check intake'
        if args.phase=='merge':
            report['merge']=await merge([r['id'] for r in report['pool']]);report['pool']=pool_rows(cids);checkpoint()
            report['details']={str(r['matched_review_id']):await request('GET','/api/review/'+str(r['matched_review_id'])) for r in report['pool'] if r['matched_review_id']}
        elif args.phase=='preview':
            matches=[r for r in report['pool'] if r['raw_question']==report['gap_question'] and r['matched_review_id']]
            assert matches,'synthetic gap has not merged'
            rid=matches[0]['matched_review_id'];report['review_id']=rid
            answer=f'仅用于 ch09 功能验收：验收专用商品 CH09-{args.run_id} 演示布袋的防伪查询口令是“墨绿猫眼”。此条为合成测试资料，不代表店铺实际商品或政策。'
            report['approved_answer']=answer
            report['approval']=await request('POST',f'/api/review/{rid}/approve',json={'approved_answer':answer});checkpoint()
            report['impact']=await request('POST','/api/review/impact',json={'review_ids':[rid]})
        elif args.phase=='publish':
            # This flag explicitly records the operator's actual check of all original cases.
            if not args.confirm_synthetic_unrelated:raise ValueError('inspect impact evidence then explicitly confirm unrelated synthetic knowledge')
            impact=report['impact'];assert len(impact['cases'])==96
            assert all(not s['affected'] for s in impact['suggestions']),'affected labels require per-case human edits'
            report['release']=await request('POST','/api/review/impact/confirm',json=dict(digest=impact['digest'],edits=[],
                unchanged_case_ids=[c['id'] for c in impact['cases']],confirmed_by='ch09-synthetic-acceptance-operator'))
            checkpoint()
            if report['release']['stage']=='complete':
                await chat('after_publish',report['gap_question'])
                report['repeat_approval']=await request('POST',f"/api/review/{report['review_id']}/approve",json={'approved_answer':report['approved_answer']})
        elif args.phase=='costs':
            report['costs']=await request('GET','/api/observability/costs',params={'purpose':'chat'})
        elif args.phase=='trace':
            report['trace_audit']=await audit_traces(report,path)
        checkpoint()
    print(json.dumps({'phase':args.phase,'report':str(path.relative_to(ROOT)),
        'pool_rows':len(report.get('pool',[])),'merge':report.get('merge'),
        'release_stage':report.get('release',{}).get('stage')}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8023')
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--phase',choices=['probe','merge','preview','publish','costs','trace','self-check'],default='probe')
    parser.add_argument('--confirm-synthetic-unrelated',action='store_true')
    parser.add_argument('--self-check-question',default='普通地区的邮费规则是什么？请同时告诉我你们营业执照上的统一社会信用代码。')
    args=parser.parse_args()
    if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}',args.base_url):raise ValueError('only verified local application allowed')
    if not re.fullmatch(r'ch09-[a-z0-9-]{1,48}',args.run_id):raise ValueError('run-id must be a synthetic ch09 identity')
    from scripts.demo_ch09 import configure
    configure()
    asyncio.run(run(args))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    try:main()
    except Exception as exc:
        print('acceptance failed: '+type(exc).__name__)
        raise SystemExit(1) from None
