"""Live local HTTP + MySQL synthetic acceptance, intentionally retains records."""
import asyncio
import json
import sys
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import httpx


async def events(client,path,body):
    result=[];buffer=''
    async with client.stream('POST',path,json=body) as response:
        response.raise_for_status()
        async for chunk in response.aiter_text():
            buffer+=chunk;buffer=buffer.replace('\r\n','\n')
            while '\n\n' in buffer:
                frame,buffer=buffer.split('\n\n',1)
                text='\n'.join(l[5:].lstrip() for l in frame.splitlines() if l.startswith('data:'))
                if text:result.append(json.loads(text))
    return result


def db_state(cid):
    from app.db import get_engine
    from app.models import Ticket,ToolAuditLog
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    with Session(get_engine()) as session:
        tickets=session.execute(select(Ticket.ticket_no).where(Ticket.conversation_id==cid)).scalars().all()
        audits=session.execute(select(ToolAuditLog.tool_call_id,ToolAuditLog.tool_name,ToolAuditLog.status,
            ToolAuditLog.tool_source,ToolAuditLog.retry_count,ToolAuditLog.duration_ms).where(ToolAuditLog.conversation_id==cid)).all()
    return {'tickets':list(tickets),'audit':[dict(r._mapping) for r in audits]}


async def main():
    from scripts.demo_ch08 import configure
    configure(require_profile=True)
    user=uuid.uuid4().hex;report={'provider':'real HTTP/model/MySQL','user_id':user,'cases':[]}
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8020',timeout=180,trust_env=False) as client:
        async def turn(question,identity=None):
            body={'session_id':'ch08-eval-'+uuid.uuid4().hex,'user_id':user,'message':question} if identity is None else {**identity,'message':question}
            frames=await events(client,'/chat',body)
            meta=next((f for f in frames if f['type']=='conversation'),None)
            identity={k:meta[k] for k in ('session_id','user_id','conversation_id')} if meta else identity
            return frames,identity
        for name,question,decision in [
            ('collect_cancel','请帮我建一个售后工单','cancel'),
            ('complete_confirm','合成验收：订单1002少了一件衣服，请帮我建售后工单','confirm'),
            ('negative_logistics','不要建工单，只查订单1001物流',None),
            ('warranty','请查询订单1001是否在保',None),
            ('return_progress','查订单1003退货进度',None),
            ('old_complaint','我要正式投诉店员态度',None),
        ]:
            row={'name':name,'question':question}
            try:
                frames,identity=await turn(question)
                row['identity']=identity;row['turns']=[frames]
                if name=='collect_cancel':
                    assert not any(f['type']=='tool_confirmation' for f in frames)
                    frames,identity=await turn('问题描述：订单1001显示签收，但我没有收到包裹。',identity)
                    row['turns'].append(frames)
                if decision:
                    preview=next(f for f in frames if f['type']=='tool_confirmation')
                    request={'session_id':identity['session_id'],'preview_id':preview['preview_id'],'decision':decision}
                    row['before']=db_state(identity['conversation_id']);assert not row['before']['tickets']
                    row['resume']=await events(client,'/api/chat/actions/tool-confirmation',request)
                    row['after']=db_state(identity['conversation_id'])
                    assert len(row['after']['tickets'])==(1 if decision=='confirm' else 0)
                    assert row['after']['audit'][-1]['status']==('成功' if decision=='confirm' else '权限拒绝')
                    if decision=='confirm':
                        repeat=await client.post('/api/chat/actions/tool-confirmation',json=request)
                        assert repeat.status_code==409 and len(db_state(identity['conversation_id'])['tickets'])==1
                        text=''.join(f.get('content','') for f in row['resume'] if f['type']=='token')
                        assert row['after']['tickets'][0] in text
                elif name=='old_complaint':
                    actions=next(f['items'] for f in frames if f['type']=='actions')
                    ticket=next(a for a in actions if a['kind']=='create_ticket')
                    request={'session_id':identity['session_id'],'action_id':ticket['action_id'],'confirmed':True}
                    response=await client.post('/api/chat/actions/ticket',json=request);response.raise_for_status()
                    again=await client.post('/api/chat/actions/ticket',json=request)
                    assert response.json()==again.json()
                    row['after']=db_state(identity['conversation_id']);assert len(row['after']['tickets'])==1
                else:
                    row['after']=db_state(identity['conversation_id']);assert not row['after']['tickets']
                    expected={'negative_logistics':'query_logistics','warranty':'query_warranty','return_progress':'query_return_progress'}[name]
                    assert any(a['tool_name']==expected and a['status']=='成功' for a in row['after']['audit'])
                assert all(f['type']!='error' for turn_frames in row['turns'] for f in turn_frames)
                row['passed']=True
            except Exception as exc:
                row.update(passed=False,error=type(exc).__name__)
            report['cases'].append(row)
            target=ROOT/'reports/ch08/integration-real.json';target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(name+': '+('pass' if row['passed'] else 'FAIL'),flush=True)
    return 0 if all(r['passed'] for r in report['cases']) else 1


if __name__=='__main__':raise SystemExit(asyncio.run(main()))
