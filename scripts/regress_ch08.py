"""Reuse frozen ch06/ch07 regressions with ch08 configuration and owned checkpoints."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


async def run(args):
    from scripts.demo_ch08 import configure
    configure(profile=args.profile,require_profile=True)
    from app.context.logging import configure_context_logging
    configure_context_logging(ROOT/'log/ch08/regression.log')
    from app.config import get_settings
    if args.task=='router':
        from scripts.eval_ch06 import evaluate_router,summarize_rows
        from app.workflow.understanding import RouterEvaluationGateway
        cases=[json.loads(l) for l in (ROOT/'docs/samples/ch06-router-acceptance.jsonl').read_text(encoding='utf-8').splitlines() if l]
        gate=asyncio.Semaphore(2)
        async def one(case):
            async with gate:
                result=await evaluate_router([case],gateway_factory=RouterEvaluationGateway)
                print('router '+str(case.get('id'))+' done',flush=True)
                return result['rows'][0]
        rows=await asyncio.gather(*(one(c) for c in cases))
        report={'task':'router','rows':rows,'summary':summarize_rows(rows)}
        report['passed']=report['summary']['passed']
    else:
        from scripts.eval_ch07 import evaluate_summaries,evaluate_dialogues
        if args.task=='summary':
            report=await evaluate_summaries('dev',get_settings());report['passed']=report['summary']['passed']
        else:
            from app.tooling.service import ToolService
            from app.tooling.policy import PolicyLoader
            service=ToolService(PolicyLoader(get_settings().tool_policy_path));await service.start()
            try:
                report=await evaluate_dialogues('dev',args.profile,get_settings(),checkpoint_dir=ROOT/'log/ch08',tool_service=service)
            finally:await service.aclose()
    report.update(provider='real',chapter='ch08',profile=args.profile)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report.get('summary',{'passed':report['passed']}),ensure_ascii=False))
    return 0 if report['passed'] else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task',choices=['router','dialogue','summary'],required=True)
    p.add_argument('--profile',choices=['default','demo'],default='demo');p.add_argument('--output',type=Path,required=True)
    return asyncio.run(run(p.parse_args()))


if __name__=='__main__':raise SystemExit(main())
