"""Local ch08 launcher. Separate calibration and checkpoint; no DDL."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def profile_values(profile):
    from scripts.demo_ch07 import profile_values as previous
    result={**previous(profile),'agent_decision_max_tokens':2600}
    if profile=='demo':
        # 18k measured layer1=442 < 2336 even before three MCP schemas.
        result['model_context_window']=24000
    return result


def retrieval_identity(*,root=ROOT):
    files=sorted((root/'app/kb').glob('*.py'))
    hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()


def configure(*,profile='default',require_profile=True):
    source=ROOT.parents[1] if ROOT.parent.name=='.worktrees' else ROOT
    load_dotenv(source/'.env')
    os.environ.update({k.upper():str(v) for k,v in profile_values(profile).items()})
    os.environ.update(CHAT_WORKFLOW_ENABLED='true',KB_CORPUS_VERSION='ch04',
        KNOWLEDGE_COLLECTION='knowledge',HYBRID_COLLECTION='knowledge_hybrid_ch04_v2_final',
        KNOWLEDGE_STRATEGY='hybrid_rerank',KB_MIGRATION_MANIFEST=str(source/'log/kb/ch04-v2-final-migration.json'),
        WORKFLOW_CHECKPOINT_PATH=str(ROOT/'log/ch08/checkpoints.sqlite'),WORKFLOW_DEBUG_VIEW='true',
        TOOL_POLICY_PATH=str(ROOT/'config/tool_policy.json'))
    from app.config import get_settings
    get_settings.cache_clear()
    from app.db import get_engine
    if get_engine().url.host not in {'localhost','127.0.0.1'}:
        raise RuntimeError('本章演示仅允许本地已核对数据库')
    target=Path(os.environ.get('CH08_CALIBRATION_PROFILE') or ROOT/'log/ch08/calibration-profile.json')
    if require_profile:
        from scripts.eval_retrieval import load_profile,_identity
        data=load_profile(target,_identity())
        if data.get('ch08_retrieval')!=retrieval_identity():
            raise ValueError('ch08检索代码身份变化，请重新校准；工具目录变化仅重新计预算')
        os.environ['RERANK_MIN_SCORE']=str(data['threshold'])
        get_settings.cache_clear()
    return target


async def budget():
    from app.config import get_settings
    from app.tooling.policy import PolicyLoader
    from app.tooling.service import ToolService
    from app.tooling.scope import model_settings
    from app.context.assembly import fixed_agent_schemas
    from app.context.budget import validate_startup,cumulative_turn_limit
    service=ToolService(PolicyLoader(get_settings().tool_policy_path))
    await service.start()
    try:
        snapshot=await service.refresh()
        settings=model_settings(get_settings(),snapshot)
        result=validate_startup(settings,schemas=fixed_agent_schemas(snapshot))
        return {**result.to_dict(),'tool_count':len(snapshot.schemas),'snapshot_hash':snapshot.version,
            'discovery_diagnostics':service.manager.diagnostics,
            'cumulative_limit':cumulative_turn_limit(settings)}
    finally:
        await service.aclose()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['budget','calibrate','serve'])
    parser.add_argument('--profile',choices=['default','demo'],default='default')
    parser.add_argument('--port',type=int,default=8020)
    args=parser.parse_args(argv)
    target=configure(profile=args.profile,require_profile=args.mode=='serve')
    if args.mode=='budget':
        print(json.dumps(asyncio.run(budget()),ensure_ascii=False));return 0
    if args.mode=='calibrate':
        from scripts.eval_retrieval import main as calibrate,load_profile,_identity
        result=calibrate(['--split','calibration','--mode','compare','--calibrate','--profile',str(target),
            '--output-dir',str(ROOT/'reports/ch08/calibration'),'--cache-dir',str(ROOT/'log/ch08/calibration-cache')])
        if result==0:
            data=load_profile(target,_identity());data['ch08_retrieval']=retrieval_identity()
            data['calibration_scope']='frozen_single_query_calibration'
            target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return result
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=args.port)
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Settings/provider exceptions can contain credentials; print type only.
        from app.context.budget import ContextBudgetError
        print(str(exc) if isinstance(exc,ContextBudgetError) else 'ch08 launcher failed: '+type(exc).__name__)
        raise SystemExit(1)
