"""Manual intake retry / one-shot sweep using the application's exact worker."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def run(args):
    from dotenv import load_dotenv
    load_dotenv(ROOT.parents[1]/'.env')
    from app.flywheel.store import PoolRepository
    from app.flywheel.worker import FlywheelWorker
    from app.flywheel.normalization import ModelNormalizer
    from app.config import get_settings
    from app.workflow.models import ModelGateway
    from app.llm import build_workflow_model
    repository=PoolRepository()
    if args.retry is not None:
        await asyncio.to_thread(repository.retry,args.retry)
    if args.once:
        from app.observability.runtime import ObservabilityRuntime
        observed=ObservabilityRuntime(get_settings());await asyncio.to_thread(observed.start)
        try:
            worker=FlywheelWorker(ModelNormalizer(ModelGateway(build_workflow_model(get_settings()))),repository=repository,
                observability=observed)
            return await worker.run_once()
        finally:await asyncio.to_thread(observed.close)
    return {'retry_requested':args.retry}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--retry',type=int)
    args=parser.parse_args()
    if not args.once and args.retry is None: parser.error('choose --once or --retry EVENT_ID')
    try: result=asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({'error_type':type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result,ensure_ascii=True))
