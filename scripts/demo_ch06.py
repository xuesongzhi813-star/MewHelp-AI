"""Local ch06 demo. Shares existing corpus, owns its SQLite and calibration files."""
import json
import hashlib
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

WORKSPACE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(WORKSPACE))


def pipeline_identity(*, root=WORKSPACE):
    paths = list((root/'app/workflow').glob('*.py'))
    paths += [root/name for name in ('app/kb/multi_query.py','app/demo_orders.py',
              'app/config.py','app/llm.py','app/schemas.py','scripts/demo_ch06.py')]
    hashes = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in paths if path.is_file()}
    return {'code_hash': hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()}


def load_ch06_profile(path, *, identity):
    from scripts.eval_retrieval import load_profile
    profile = load_profile(path, identity)
    if profile.get('ch06_pipeline') != pipeline_identity():
        raise ValueError('ch06管线身份缺失或已变化，请用原calibration集重新生成独立profile。')
    return profile


def bind_calibrated_profile(path):
    from scripts.eval_retrieval import _identity, load_profile
    # Only called after a successful fresh calibration, never to bless an old identity.
    profile = load_profile(path, _identity())
    profile.update(ch06_pipeline=pipeline_identity(), calibration_scope='existing_single_query_calibration')
    path.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def configure(*, require_profile=True):
    source=WORKSPACE.parents[1] if WORKSPACE.parent.name=='.worktrees' else WORKSPACE
    load_dotenv(source/'.env')
    profile=Path(os.environ.get('CH06_CALIBRATION_PROFILE') or WORKSPACE/'log/ch06/calibration-profile.json')
    os.environ.update(CHAT_WORKFLOW_ENABLED='true',KB_CORPUS_VERSION='ch04',
        KNOWLEDGE_COLLECTION='knowledge',HYBRID_COLLECTION='knowledge_hybrid_ch04_v2_final',
        KNOWLEDGE_STRATEGY='hybrid_rerank',KB_MIGRATION_MANIFEST=str(source/'log/kb/ch04-v2-final-migration.json'),
        WORKFLOW_CHECKPOINT_PATH=str(WORKSPACE/'log/ch06/checkpoints.sqlite'),WORKFLOW_DEBUG_VIEW='true')
    from app.config import get_settings
    get_settings.cache_clear()
    from app.db import get_engine
    if get_engine().url.host not in {'localhost','127.0.0.1'}:
        raise RuntimeError('演示仅允许已核对的本地数据库。')
    if require_profile:
        from scripts.eval_retrieval import _identity
        validated=load_ch06_profile(profile,identity=_identity())
        os.environ['RERANK_MIN_SCORE']=str(validated['threshold'])
        get_settings.cache_clear()
    return profile


def main():
    mode=sys.argv.pop(1) if len(sys.argv)>1 else 'serve'
    if mode=='calibrate':
        profile=configure(require_profile=False)
        from scripts.eval_retrieval import main as calibrate
        result=calibrate(['--split','calibration','--mode','compare','--calibrate',
            '--profile',str(profile),'--output-dir',str(WORKSPACE/'reports/ch06/calibration'),
            '--cache-dir',str(WORKSPACE/'log/ch06/calibration-cache')])
        if result == 0:
            bind_calibrated_profile(profile)
        return result
    if mode=='router-eval':
        configure(require_profile=False)
        from scripts.eval_ch06 import main as evaluate
        return evaluate()
    if mode!='serve':
        raise ValueError('请选择serve、calibrate或router-eval。')
    configure()
    log_dir=WORKSPACE/'log/ch06'
    log_dir.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger('app.workflow')
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.FileHandler(log_dir/'nodes.log',encoding='utf-8'))
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=8016)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
