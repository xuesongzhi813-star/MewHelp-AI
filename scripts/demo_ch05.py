"""Local ch05 demo using the existing root configuration and ch04 corpus."""
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))


def configure():
    # This chapter's isolated worktree deliberately shares local credentials.
    source = WORKSPACE.parents[1] if WORKSPACE.parent.name == '.worktrees' else WORKSPACE
    load_dotenv(source / '.env')
    profile_path = Path(os.environ.get('CH05_CALIBRATION_PROFILE') or source / 'log/kb/ch04-v2-final-calibration.json')
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    os.environ.update(
        CHAT_WORKFLOW_ENABLED='true', KB_CORPUS_VERSION='ch04', KNOWLEDGE_COLLECTION='knowledge',
        HYBRID_COLLECTION='knowledge_hybrid_ch04_v2_final', KNOWLEDGE_STRATEGY='hybrid_rerank',
        KB_MIGRATION_MANIFEST=str(source / 'log/kb/ch04-v2-final-migration.json'),
        RERANK_MIN_SCORE=str(profile['threshold']),
        WORKFLOW_CHECKPOINT_PATH=str(WORKSPACE / 'log/ch05/checkpoints.sqlite'),
    )
    from app.db import get_engine
    if get_engine().url.host not in {'localhost', '127.0.0.1'}:
        raise RuntimeError('This demo requires the existing local database')
    log_dir = WORKSPACE / 'log/ch05'
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('app.workflow')
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.FileHandler(log_dir / 'nodes.log', encoding='utf-8'))


if __name__ == '__main__':
    configure()
    if len(sys.argv) > 1:
        # Reuse the normal modules and their argument parsers for read-only demos.
        import runpy
        module = {'agent': 'app.agent_demo', 'eval': 'scripts.eval_ch05'}[sys.argv.pop(1)]
        runpy.run_module(module, run_name='__main__')
    else:
        import uvicorn
        uvicorn.run('app.main:app', host='127.0.0.1', port=8015)
