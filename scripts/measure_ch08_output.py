"""Provider measurement: complete legal ticket arguments without executing writes."""
import asyncio
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


async def main():
    from scripts.demo_ch08 import configure
    configure(require_profile=True)
    from app.config import get_settings
    from app.llm import build_workflow_model
    from app.tooling.service import ToolService
    from app.tooling.policy import PolicyLoader
    from app.tooling.scope import tool_scope
    from app.workflow.models import ModelGateway
    from app.workflow.agent import AGENT_PROMPT
    from app.workflow.contracts import BudgetState
    from langchain_core.messages import SystemMessage,HumanMessage
    service=ToolService(PolicyLoader(get_settings().tool_policy_path));await service.start();rows=[]
    try:
        snapshot=await service.refresh()
        for name,description in [('cjk2000',('订单1001未收到包裹，系统却显示已签收。'*150)[:2000]),
            ('escaped_literal',('问题包含字符\\u4e00和双引号"，请核实。'*100)[:2000])]:
            args={'description':description,'ticket_type':'售后'};budget=BudgetState(contextual=True,token_limit=50000)
            row={'case':name,'characters':len(description)}
            try:
                with tool_scope(service,snapshot,{}):
                    response=await ModelGateway(build_workflow_model(get_settings())).decide([
                        SystemMessage(AGENT_PROMPT),HumanMessage('请建工单。系统verified_ticket_draft数据：'+json.dumps({**args,'status':'ready','missing':[]},ensure_ascii=False))],budget)
                calls=response.tool_calls;row['passed']=len(calls)==1 and calls[0]['name']=='create_ticket' and calls[0]['args']==args
                row['returned_characters']=len(calls[0]['args'].get('description','')) if calls else 0
            except Exception as exc:row.update(passed=False,error=type(exc).__name__)
            row['usage']=budget.model_dump();rows.append(row);print(name+': '+str(row['passed']),flush=True)
    finally:await service.aclose()
    (ROOT/'reports/ch08/output-capacity-real.json').write_text(json.dumps({'provider':'real model; no tool execution','rows':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if all(r['passed'] for r in rows) else 1


if __name__=='__main__':raise SystemExit(asyncio.run(main()))
