"""Use the online owner API, or own the corpus offline for a real held-out run."""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys
import os

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def offline(trigger,slot):
    from app.config import get_settings
    from app.observability.runtime import ObservabilityRuntime
    from app.flywheel.publishing import application_owner,version_store,ReleaseCoordinator,load_active
    from app.evaluation.release import validate_release,load_policy
    from app.evaluation.runner import EvaluationRunner
    from app.evaluation.executor import CaseExecutor
    observed=ObservabilityRuntime(get_settings());await asyncio.to_thread(observed.start)
    try:
        async with application_owner() as owner:
            versions=version_store()
            def validate(identity):owner.assert_owned();validate_release(versions,identity)
            coordinator=ReleaseCoordinator(active=lambda:load_active(versions),validator=validate,
                policy_loader=lambda identity:load_policy(versions,identity))
            runner=EvaluationRunner(versions,coordinator,CaseExecutor(coordinator,observability=observed),observability=observed)
            return await runner.run_evaluation(trigger=trigger,slot=slot)
    finally:await asyncio.to_thread(observed.close)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trigger',choices=['手动','定时'],default='手动')
    parser.add_argument('--slot')
    parser.add_argument('--no-cache',action='store_true',help='Explicit fresh generation/judge evaluation; every ch09 round is uncached.')
    parser.add_argument('--url',default=os.environ.get('CH09_API_URL','http://127.0.0.1:8023'))
    args=parser.parse_args(argv)
    from scripts.demo_ch09 import configure
    configure(require_active=True)
    from app.flywheel.publishing import owner_present
    if owner_present():
        if args.trigger!='手动' or args.slot:raise ValueError('online scheduled slots are managed by application')
        if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}',args.url):raise ValueError('only loopback URL allowed')
        import httpx
        response=httpx.post(args.url+'/api/evaluations/run',json={'trigger':'手动'},timeout=3600)
        response.raise_for_status();result=response.json()
    else:result=asdict(asyncio.run(offline(args.trigger,args.slot)))
    print(json.dumps(result,ensure_ascii=True))
    return 0 if result.get('status')=='success' else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('ch09 evaluation failed: '+type(exc).__name__)
        raise SystemExit(1) from None
