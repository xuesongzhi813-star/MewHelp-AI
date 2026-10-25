"""Local ch07 entrypoint. Own profile/checkpoint; existing MySQL/Milvus corpus."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

WORKSPACE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(WORKSPACE))


def profile_values(profile):
    default=dict(model_context_window=128000,max_output_tokens=4000,max_user_input_tokens=4000,
                 max_agent_steps=8,tool_result_max_tokens=2400,rerank_top_k=10)
    if profile=='demo':
        return dict(model_context_window=18000,max_output_tokens=2000,max_user_input_tokens=2000,
                    max_agent_steps=3,tool_result_max_tokens=1200,rerank_top_k=5)
    if profile!='default':
        raise ValueError('unknown profile')
    return default


def pipeline_identity(*, root=WORKSPACE):
    files=list((root/'app/context').glob('*.py'))+list((root/'app/workflow').glob('*.py'))
    files += [root/p for p in ('app/config.py','app/llm.py','app/schemas.py','scripts/demo_ch07.py')]
    hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return {'code_hash':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()}


def load_ch07_profile(path, *, identity):
    from scripts.eval_retrieval import load_profile
    result=load_profile(path,identity)
    if result.get('ch07_pipeline')!=pipeline_identity():
        raise ValueError('ch07管线身份已变化，请用冻结calibration集重新生成本章profile。')
    return result


def configure(*, profile='default', require_profile=True):
    source=WORKSPACE.parents[1] if WORKSPACE.parent.name=='.worktrees' else WORKSPACE
    load_dotenv(source/'.env')
    os.environ.update({k.upper():str(v) for k,v in profile_values(profile).items()})
    os.environ.update(CHAT_WORKFLOW_ENABLED='true',KB_CORPUS_VERSION='ch04',
        KNOWLEDGE_COLLECTION='knowledge',HYBRID_COLLECTION='knowledge_hybrid_ch04_v2_final',
        KNOWLEDGE_STRATEGY='hybrid_rerank',KB_MIGRATION_MANIFEST=str(source/'log/kb/ch04-v2-final-migration.json'),
        WORKFLOW_CHECKPOINT_PATH=str(WORKSPACE/'log/ch07/checkpoints.sqlite'),WORKFLOW_DEBUG_VIEW='true')
    from app.config import get_settings
    get_settings.cache_clear()
    from app.db import get_engine
    if get_engine().url.host not in {'localhost','127.0.0.1'}:
        raise RuntimeError('演示仅允许本地已核对数据库。')
    target=Path(os.environ.get('CH07_CALIBRATION_PROFILE') or WORKSPACE/'log/ch07/calibration-profile.json')
    if require_profile:
        from scripts.eval_retrieval import _identity
        data=load_ch07_profile(target,identity=_identity())
        os.environ['RERANK_MIN_SCORE']=str(data['threshold'])
        get_settings.cache_clear()
    return target


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['budget','serve','calibrate','evaluate'])
    parser.add_argument('--profile',choices=['default','demo'],default='default')
    parser.add_argument('--port',type=int,default=8018)
    args,remaining=parser.parse_known_args(argv)
    if args.mode=='evaluate':
        from scripts.eval_ch07 import main as evaluate
        return evaluate(['--profile',args.profile,*remaining])
    target=configure(profile=args.profile,require_profile=args.mode=='serve')
    if args.mode=='budget':
        from app.config import get_settings
        from app.context.budget import validate_startup,cumulative_turn_limit
        from app.context.assembly import fixed_agent_schemas
        result=validate_startup(get_settings(),schemas=fixed_agent_schemas())
        print(json.dumps(dict(profile=args.profile,**result.to_dict(),cumulative_limit=cumulative_turn_limit(get_settings())),ensure_ascii=False))
        return 0
    if args.mode=='calibrate':
        from scripts.eval_retrieval import main as calibrate,load_profile,_identity
        result=calibrate(['--split','calibration','--mode','compare','--calibrate','--profile',str(target),
            '--output-dir',str(WORKSPACE/'reports/ch07/calibration'),
            '--cache-dir',str(WORKSPACE/'log/ch07/calibration-cache')])
        if result==0:
            data=load_profile(target,_identity())
            data.update(ch07_pipeline=pipeline_identity(),calibration_scope='frozen_single_query_calibration')
            target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return result
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=args.port)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
