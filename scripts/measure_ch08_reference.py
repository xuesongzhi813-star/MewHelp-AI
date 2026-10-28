"""Real model/native interrupt/MySQL proof of complete 2000-character source references."""
import asyncio
import json
import sys
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import httpx


async def main():
    from scripts.demo_ch08 import configure
    configure(require_profile=True)
    from scripts.integrate_ch08 import events,db_state
    from app.db import get_engine
    from app.models import Ticket
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    rows=[];user=uuid.uuid4().hex
    samples=[('cjk2000',('订单1001未收到包裹，系统却显示已签收。'*150)[:2000]),
        ('escaped2000',(r'问题包含字符\u4e00和双引号"，请核实。'*100)[:2000])]
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8020',timeout=180,trust_env=False) as client:
        for name,description in samples:
            row={'case':name,'characters':len(description)}
            try:
                first=await events(client,'/chat',{'session_id':'ch08-long-'+uuid.uuid4().hex,
                    'user_id':user,'message':'帮我建一个售后工单'})
                identity=next(e for e in first if e['type']=='conversation')
                row.update(conversation_id=identity['conversation_id'],session_id=identity['session_id'],first=first)
                assert not any(e['type']=='tool_confirmation' for e in first)
                body={k:identity[k] for k in ('session_id','user_id','conversation_id')}
                frames=await events(client,'/chat',{**body,'message':description});row['description_turn']=frames
                preview=next(e for e in frames if e['type']=='tool_confirmation')
                assert preview['description']==description
                row['resume']=await events(client,'/api/chat/actions/tool-confirmation',{
                    'session_id':identity['session_id'],'preview_id':preview['preview_id'],'decision':'confirm'})
                database=db_state(identity['conversation_id']);row['database']=database
                assert len(database['tickets'])==1
                with Session(get_engine()) as session:
                    persisted=session.execute(select(Ticket.description).where(
                        Ticket.conversation_id==identity['conversation_id'])).scalar_one()
                row['persisted_characters']=len(persisted);assert persisted==description
                assert database['tickets'][0] in ''.join(e.get('content','') for e in row['resume'] if e['type']=='token')
                row['passed']=True
            except Exception as exc:row.update(passed=False,error=type(exc).__name__)
            rows.append(row)
            (ROOT/'reports/ch08/output-capacity-reference-real.json').write_text(json.dumps({
                'provider':'real HTTP/model/native interrupt/MySQL','user_id':user,'rows':rows},
                ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(name+': '+str(row['passed']),flush=True)
    return 0 if all(r['passed'] for r in rows) else 1


if __name__=='__main__':raise SystemExit(asyncio.run(main()))
