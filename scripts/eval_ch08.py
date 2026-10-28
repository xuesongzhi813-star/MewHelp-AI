"""Frozen semantic routing evaluation. Real workflow writes are verified separately."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def load_cases(split):
    path=ROOT/f'docs/samples/ch08-tools-{split}.jsonl'
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    bound=path.with_suffix('.sha256')
    if bound.exists() and bound.read_text().strip()!=digest:
        raise ValueError('frozen sample hash mismatch')
    cases=[json.loads(row) for row in path.read_text(encoding='utf-8').splitlines() if row]
    if len(cases)!=dict(dev=24,acceptance=36)[split] or len({c['question'] for c in cases})!=len(cases):
        raise ValueError('invalid frozen cases')
    for case in cases:
        if case['expected_operation'] not in {'none','create_ticket','read_tool'}:
            raise ValueError('invalid expected operation')
        if case['expected_route'] not in {'business','knowledge','refund','complaint','chat','other'}:
            raise ValueError('invalid expected route')
        if (case['expected_operation']=='read_tool') != bool(case['expected_candidate']):
            raise ValueError('invalid candidate label')
    return cases,digest


def score_case(case,result,*,mode):
    passed=bool(not result.get('service_error') and result.get('safe') is True
        and result.get('operation')==case['expected_operation']
        and (case['expected_candidate'] is None or result.get('candidate')==case['expected_candidate'])
        and result.get('route')==case['expected_route'])
    return {'passed':passed,'quality_verified':mode=='real' and passed}


async def evaluate(args):
    from scripts.demo_ch08 import configure
    configure(require_profile=False)
    from app.config import get_settings
    from app.tooling.policy import PolicyLoader
    from app.tooling.service import ToolService
    from app.tooling.scope import tool_scope
    from app.workflow.models import ModelGateway
    from app.workflow.contracts import BudgetState
    from app.workflow.understanding import understand_question
    from app.workflow.ticket_draft import validate_ticket_draft
    from app.workflow.routing import classify_intent,route_with_tools
    from app.llm import build_workflow_model
    from langchain_core.messages import HumanMessage,AIMessage
    cases,digest=load_cases(args.split)
    service=ToolService(PolicyLoader(get_settings().tool_policy_path))
    await service.start();rows=[];semaphore=asyncio.Semaphore(2)
    try:
        snapshot=await service.refresh()
        async def one(case):
            async with semaphore:
                result={'safe':True,'service_error':None}
                if args.mode=='mock':
                    result.update(operation=case['expected_operation'],candidate=case['expected_candidate'],route=case['expected_route'])
                else:
                    gateway=ModelGateway(build_workflow_model(get_settings()));budget=BudgetState(token_limit=50000)
                    history=[(HumanMessage if h['role']=='user' else AIMessage)(h['content'],id=h['id']) for h in case['history']]
                    current=max([int(h['id'].split(':')[-1]) for h in case['history']] or [0])+1
                    user_rows=[SimpleNamespace(id=int(h['id'].split(':')[-1]),role=h['role'],content=h['content'],conversation_id=1) for h in case['history']]
                    user_rows.append(SimpleNamespace(id=current,role='user',content=case['question'],conversation_id=1))
                    try:
                        with tool_scope(service,snapshot,{}):
                            understood=await understand_question(case['question'],history=history,gateway=gateway,budget=budget,
                                current_message_id=f'db:{current}',draft=case.get('prior_draft'))
                            draft=validate_ticket_draft(understood,user_rows=user_rows,prior_draft=case.get('prior_draft'),current_turn=current)
                            data=understood.model_dump()
                            if draft:data.update(requested_operation='create_ticket',ticket_request_verified=True)
                            intent=await classify_intent(understood.standalone_question,gateway=gateway,budget=budget)
                            result.update(operation=data['requested_operation'],candidate=data['candidate_tool'],intent=intent.intent,
                                route=route_with_tools(intent.intent,data,snapshot,service.policy_loader.load()),
                                draft_status=draft.status if draft else None,usage=budget.model_dump(),
                                safe=not draft if case['expected_operation']!='create_ticket' else True)
                    except Exception as exc:
                        result['service_error']=type(exc).__name__
                row={**case,**result,**score_case(case,result,mode=args.mode)}
                print(case['id']+': '+('pass' if row['passed'] else 'FAIL'),flush=True)
                return row
        rows=await asyncio.gather(*(one(c) for c in cases))
    finally:
        await service.aclose()
    report={'mode':args.mode,'scope':'semantic_only_no_business_writes','split':args.split,'sha256':digest,
        'snapshot_hash':snapshot.version,'passed':sum(r['passed'] for r in rows),'total':len(rows),'rows':rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"{report['passed']}/{report['total']} ({args.mode})")
    return 0 if report['passed']==report['total'] else 1


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--split',choices=['dev','acceptance'],default='dev')
    p.add_argument('--mode',choices=['mock','real'],default='mock');p.add_argument('--output',type=Path,default=ROOT/'reports/ch08/evaluation.json')
    return asyncio.run(evaluate(p.parse_args(argv)))


if __name__=='__main__':raise SystemExit(main())
