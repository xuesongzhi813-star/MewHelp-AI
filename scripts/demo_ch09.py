"""Independent ch09 entry point; never writes the ch08 profile or checkpoint."""
import argparse
import os
from pathlib import Path
import socket
import sys
import asyncio
import json
import re

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def verify_local_target():
    from app.db import get_engine
    from app.config import get_settings
    get_engine.cache_clear()
    if get_engine().url.host not in {'localhost','127.0.0.1'}:
        raise RuntimeError('ch09 demo requires verified local database')
    if get_settings().langfuse_base_url.rstrip('/')!='https://jp.cloud.langfuse.com':
        raise RuntimeError('ch09 requires the configured Japan Langfuse project region')


def configure(*,require_active=True):
    from scripts.demo_ch08 import configure as previous
    previous(require_profile=False)
    os.environ.update(CH09_ENABLED='true',FLYWHEEL_ENABLED='true',
        CH09_DATA_ROOT=str(ROOT/'data/local/ch09'),WORKFLOW_CHECKPOINT_PATH=str(ROOT/'log/ch09/checkpoints.sqlite'))
    from app.config import get_settings
    get_settings.cache_clear();verify_local_target()
    if require_active:
        from app.flywheel.publishing import load_active
        load_active()


def check_port(port):
    with socket.socket() as server:
        server.bind(('127.0.0.1',port))


async def calibrate_offline(mode):
    from app.flywheel.publishing import application_owner,version_store,PUBLISH_LOCK,async_lock_scope
    from app.evaluation.release import bootstrap,recalibrate_current
    from app.observability.runtime import ObservabilityRuntime
    from app.observability.spans import execution_scope
    from app.config import get_settings
    observed=ObservabilityRuntime(get_settings());await asyncio.to_thread(observed.start)
    try:
        with execution_scope('calibration',observed,purpose='calibration'):
            async with application_owner() as owner:
                async with async_lock_scope(PUBLISH_LOCK) as publication:
                    def assert_owned():owner.assert_owned();publication.assert_owned()
                    return await (bootstrap if mode=='bootstrap' else recalibrate_current)(version_store(),assert_owned=assert_owned)
    finally:await asyncio.to_thread(observed.close)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['check','bootstrap','calibrate','serve'])
    parser.add_argument('--port',type=int,default=8023)
    parser.add_argument('--url',default='http://127.0.0.1:8023')
    args=parser.parse_args(argv)
    configure(require_active=args.mode=='serve')
    from app.flywheel.publishing import owner_present,version_store
    from app.config import get_settings
    if args.mode=='check':
        settings=get_settings()
        print(json.dumps({'ch09_enabled':True,'owner_online':owner_present(),
            'active_exists':version_store().artifacts.exists('active.json'),
            'langfuse_enabled':settings.langfuse_enabled,
            'langfuse_keys_configured':bool(settings.langfuse_public_key and settings.langfuse_secret_key),
            'langfuse_region':'jp','checkpoint':settings.workflow_checkpoint_path},ensure_ascii=True))
        return 0
    if args.mode in {'bootstrap','calibrate'}:
        if owner_present():
            if args.mode=='bootstrap':raise RuntimeError('online owner exists; bootstrap requires offline ownership')
            if not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}',args.url):raise ValueError('only loopback application URL allowed')
            import httpx
            response=httpx.post(args.url+'/api/review/recalibrate',timeout=600)
            response.raise_for_status();print(json.dumps(response.json(),ensure_ascii=True));return 0
        from dataclasses import asdict
        result=asyncio.run(calibrate_offline(args.mode))
        print(json.dumps(asdict(result),ensure_ascii=True));return 0
    check_port(args.port)
    os.environ['CH09_API_URL']=f'http://127.0.0.1:{args.port}'
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=args.port)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('ch09 launcher failed: '+type(exc).__name__)
        raise SystemExit(1) from None
