"""Live FastAPI hotplug proof; restores local policy bytes and own test server."""
import asyncio
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import httpx


async def main():
    from scripts.demo_ch08 import configure
    configure(require_profile=True)
    from tests.test_mcp_servers import running_server
    from app.tooling.mcp_client import connect_session
    from scripts.integrate_ch08 import events,db_state
    import uuid,subprocess
    def app_pid():
        value=subprocess.check_output(['powershell','-NoProfile','-Command',
            '(Get-NetTCPConnection -LocalPort 8020 -State Listen).OwningProcess'],text=True)
        return int(value.strip())
    policy_path=ROOT/'config/tool_policy.json';original=policy_path.read_bytes()
    config=json.loads(original);rows=[];user=uuid.uuid4().hex
    report={'provider':'real HTTP/model/MCP/MySQL','app_pid_before':app_pid(),'user_id':user,'steps':rows}
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8020',timeout=180,trust_env=False) as client:
        async def query(label,name,expected):
            frames=await events(client,'/chat',{'session_id':'ch08-hotplug-'+uuid.uuid4().hex,'user_id':user,
                'message':f'查询订单1001，请使用{name}工具，参数id为1001，不要建工单。'})
            identity=next(f for f in frames if f['type']=='conversation')
            database=db_state(identity['conversation_id'])
            success=any(a['tool_name']==name and a['status']=='成功' for a in database['audit'])
            row={'step':label,'conversation_id':identity['conversation_id'],'frames':frames,'database':database,
                'passed':success==expected and not database['tickets'] and not any(f['type']=='error' for f in frames)}
            rows.append(row);print(label+': '+('pass' if row['passed'] else 'FAIL'),flush=True)
        def save_policy():policy_path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        try:
            async with running_server('tests.mcp_dynamic_server',ROOT/'log/ch08',port=8023) as url:
                config['servers']['probe']={'url':url,'enabled':True}
                config['rules']['probe/first']={'enabled':True,'effect':'read','routing_intents':['订单','其他'],'output_fields':['id','server_pid']}
                save_policy();await query('initial-live-tool','mcp__probe__first',True)
                async with connect_session(url) as admin:
                    await admin.initialize();added=await admin.call_tool('test_add',{})
                    report['mcp_pid']=added.structuredContent['server_pid']
                    await query('new-tool-default-deny','mcp__probe__added',False)
                    config['rules']['probe/added']={'enabled':True,'effect':'read','routing_intents':['订单','其他'],'output_fields':['id','server_pid']}
                    save_policy();await query('authorize-live-added','mcp__probe__added',True)
                    config['rules']['probe/added']['enabled']=False;save_policy()
                    await query('revoke-live-added','mcp__probe__added',False)
            await query('server-offline','mcp__probe__first',False)
            async with running_server('tests.mcp_dynamic_server',ROOT/'log/ch08',port=8023):
                await query('same-endpoint-reconnect','mcp__probe__first',True)
        finally:
            policy_path.write_bytes(original)
            report['policy_restored']=policy_path.read_bytes()==original
            report['app_pid_after']=app_pid()
            report['passed']=bool(rows and all(r['passed'] for r in rows) and report['policy_restored'] and report['app_pid_before']==report['app_pid_after'])
            (ROOT/'reports/ch08/hotplug-real.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(asyncio.run(main()))
