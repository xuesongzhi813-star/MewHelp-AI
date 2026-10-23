"""固定分割的四策略离线评估；无业务会话或低置信度池写入。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from app.config import get_settings
from app.kb.answering import generate_answer, make_evidence, refuse
from app.kb.contracts import RetrievalResult
from app.kb.embedding import embed_texts
from app.kb.evaluation import (aggregate_results, bind_cases, load_cases,
                               load_eval_manifest, retrieval_metrics)
from app.kb.evaluation_judge import (FAITHFULNESS_PROMPT, REQUIRED_FACTS_PROMPT,
    judge_faithfulness, judge_required_facts)
from app.kb.query import build_query_plan
from app.kb.retriever import retrieve_result
from app.prompts import EVIDENCE_ANSWER_PROMPT, QUERY_UNDERSTANDING_PROMPT

STRATEGIES = ('dense', 'bm25', 'hybrid', 'hybrid_rerank')


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _runtime_corpus_identity() -> str:
    from app.kb import migration, store
    manifest = migration.load_manifest()
    if manifest.phase != 'validated':
        raise ValueError('评估需要已验证的ch04迁移')
    before_ids = migration.eligible_chunk_ids()
    rows = sorted(store.iter_chunks_by_ids(before_ids), key=lambda row: row.id)
    if migration.verify_hybrid()['status'] != 'consistent':
        raise ValueError('评估需要MySQL/Milvus语料一致')
    after_rows = sorted(store.iter_chunks_by_ids(migration.eligible_chunk_ids()), key=lambda row: row.id)
    if rows != after_rows:
        raise ValueError('读取快照期间语料发生变化')
    return _digest({'collection': manifest.target_collection,
                    'rows': [{key: str(value) if key == 'created_at' else value
                              for key, value in asdict(row).items()} for row in rows]})


def _identity() -> dict:
    settings = get_settings()
    manifest = load_eval_manifest()
    root = Path(__file__).resolve().parents[1]
    modules = ['app/kb/' + name + '.py' for name in ('answering','query','retriever','reranker',
                'evaluation','evaluation_judge','milvus','filters','catalog','embedding')]
    modules += ['scripts/eval_retrieval.py', 'app/kb/contracts.py']
    return {'evaluation_version': manifest['evaluation_version'], 'corpus_hash': manifest['corpus_hash'],
            'cases_sha256': manifest['cases_sha256'], 'runtime_corpus_hash': _runtime_corpus_identity(),
            'llm_model': settings.llm_model,
            'embedding_model': settings.embedding_model, 'rerank_model': settings.rerank_model,
            'temperature': 0, 'dense_top_k': 50, 'bm25_top_k': 50, 'rrf_k': 60, 'rerank_top_k': 10,
            'model_parameters_hash': _digest([settings.llm_extra_body, settings.llm_base_url,
                                               settings.embedding_base_url, settings.rerank_base_url]),
            'prompt_hash': _digest([QUERY_UNDERSTANDING_PROMPT,EVIDENCE_ANSWER_PROMPT,FAITHFULNESS_PROMPT,REQUIRED_FACTS_PROMPT]),
            'code_hash': _digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in modules})}


def _load_bound_cases(split):
    from app.kb import migration, store
    from app.kb.sources import get_sources
    manifest = migration.load_manifest()
    if manifest.phase != 'validated':
        raise ValueError('评估需要已验证的ch04迁移')
    cases = [case for case in load_cases() if case.split == split]
    rows = list(store.iter_chunks_by_ids(migration.eligible_chunk_ids()))
    return bind_cases(cases, get_sources(corpus_version='ch04'), rows)


def _models():
    from app.llm import build_answer_model, build_judge_models
    settings = get_settings()
    return build_answer_model(settings), *build_judge_models(settings)


class UsageRecorder:
    def __init__(self, model):
        self.model, self.last_usage = model, None

    async def ainvoke(self, messages):
        self.last_usage = None
        response = await self.model.ainvoke(messages)
        if isinstance(response, dict) and 'raw' in response:
            self.last_usage = getattr(response['raw'], 'usage_metadata', None)
        return response


def load_profile(path: Path, identity: dict) -> dict:
    profile = json.loads(path.read_text(encoding='utf-8'))
    if profile.get('identity') != identity or profile.get('split') != 'calibration':
        previous = profile.get('identity') or {}
        fields = sorted(k for k in set(previous) | set(identity) if previous.get(k) != identity.get(k))
        if profile.get('split') != 'calibration':
            fields.append('split')
        raise ValueError('冻结profile与当前语料/模型/Prompt/代码hash不符；差异字段：' + ', '.join(fields)
                         + '。知识新增或修改后需重新校准，请保留旧profile，不要强改hash。')
    if not isinstance(profile.get('threshold'), (int,float)) or not .1 <= profile['threshold'] <= .9:
        raise ValueError('profile阈值非法')
    return profile


async def _run(*, split, mode, output_dir, cache_dir, strategies, profile):
    if get_settings().kb_corpus_version != 'ch04':
        raise ValueError('正式评估须显式激活ch04语料，禁止误用旧集合')
    if split not in ('calibration','test') or mode not in ('compare','deployment'):
        raise ValueError('split/mode非法')
    if not strategies or any(s not in STRATEGIES for s in strategies):
        raise ValueError('策略非法')
    identity = _identity()
    threshold = None
    if mode == 'deployment':
        if profile is None or tuple(strategies) != ('hybrid_rerank',):
            raise ValueError('deployment只允许hybrid_rerank且必须使用冻结profile')
        threshold = load_profile(Path(profile), identity)['threshold']
    bound = _load_bound_cases(split)
    answer_model, judge_model, required_model = [UsageRecorder(m) for m in _models()]
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in bound:
        case = item.case
        plan_start = time.monotonic()
        plan = build_query_plan(case.raw_query, filters=case.filters)
        plan_seconds = time.monotonic() - plan_start
        vector = None
        embedding_error = None
        embedding_seconds = 0
        if any(s != 'bm25' for s in strategies) and plan.normalized_query.strip():
            embedding_start = time.monotonic()
            try:
                vector = embed_texts([plan.normalized_query])[0]
            except Exception as exc:
                embedding_error = exc
            embedding_seconds = time.monotonic() - embedding_start
        for strategy in strategies:
            key = _digest({'identity': identity, 'case': case.model_dump(mode='json'),
                           'plan': plan.model_dump(mode='json'), 'strategy': strategy,
                           'mode': mode, 'threshold': threshold})
            cache_path = cache_dir / (key + '.json')
            if cache_path.exists():
                cached = json.loads(cache_path.read_text(encoding='utf-8'))
                if cached.get('cache_key') == key:
                    cached['cache_hit'] = True
                    rows.append(cached)
                    continue
            start = time.monotonic()
            row = {'id': case.id, 'bucket': case.bucket, 'difficulty': case.difficulty,
                   'split': split, 'strategy': strategy, 'answerable': case.answerable,
                   'plan': plan.model_dump(mode='json'), 'cache_key': key, 'cache_hit': False,
                   'can_answer': None, 'faithfulness': None, 'required_facts_total': len(case.required_facts),
                   'required_facts_covered': 0, 'required_facts': case.required_facts,
                   'relevant_ids': sorted(item.relevant_ids), 'error': None, 'judge_error': None}
            row.update(shared_query_seconds=round(plan_seconds,3),
                       shared_embedding_seconds=round(embedding_seconds,3) if strategy != 'bm25' else 0)
            try:
                if strategy != 'bm25' and embedding_error is not None:
                    raise embedding_error
                result = retrieve_result(case.raw_query, strategy=strategy, plan=plan,
                                         query_vector=vector if strategy != 'bm25' else None)
                row['candidate_ids'] = list(result.candidate_ids)
                row['hits'] = [asdict(h) for h in result.hits]
                ranking = [h.chunk_id for h in result.hits]
                row['recall_at_5'] = retrieval_metrics(ranking, item.relevant_ids, k=5)['recall']
                metric = retrieval_metrics(ranking, item.relevant_ids, k=10)
                row.update(recall_at_10=metric['recall'], mrr_at_10=metric['mrr'], all_required_hit=metric['all_required_hit'])
                row['candidate_recall_at_50'] = retrieval_metrics(result.candidate_ids, item.relevant_ids, k=50)['recall']
                row['final_recall_at_50'] = None if strategy == 'hybrid_rerank' else retrieval_metrics(ranking, item.relevant_ids, k=50)['recall']
                row['max_rerank_score'] = result.hits[0].rerank_score if strategy == 'hybrid_rerank' and result.hits else None
                # 公平对比关闭专属分数闸门；部署用同一生成模块及已冻结的单独阈值。
                answer_model.last_usage = None
                if mode == 'deployment' and result.hits and (row['max_rerank_score'] is None or row['max_rerank_score'] < threshold):
                    decision = refuse('重排分数低于冻结部署阈值')
                else:
                    decision = await generate_answer(plan, result, model=answer_model, apply_score_gate=False)
                row.update(decision=decision.model_dump(), can_answer=decision.can_answer,
                           answer_usage=answer_model.last_usage)
                if decision.can_answer:
                    faithful = await judge_faithfulness(case.raw_query, decision.answer, make_evidence(result.hits), model=judge_model)
                    row.update(faithfulness=faithful.model_dump(), judge_error=faithful.error, judge_usage=judge_model.last_usage)
                    try:
                        covered = await judge_required_facts(decision.answer, case.required_facts, model=required_model)
                        row.update(required_facts_covered=sum(covered), required_facts_decisions=covered,
                                   required_facts_usage=required_model.last_usage)
                    except Exception:
                        row['required_facts_error'] = '评审失败'
            except Exception as exc:
                row['error'] = type(exc).__name__  # 不写provider异常正文或凭据。
            row['elapsed_seconds'] = round(time.monotonic() - start, 3)
            # 故障不缓存，恢复服务后可以重新评估；成功拒答可以缓存。
            if not row['error'] and not row['judge_error'] and not row.get('required_facts_error'):
                cache_path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            rows.append(row)
            print(f"{case.id} {strategy}: {'error' if row['error'] else row['can_answer']}", flush=True)
    if _runtime_corpus_identity() != identity['runtime_corpus_hash']:
        raise ValueError('评估期间语料发生变化，本轮结果不可比较，请恢复固定语料后重跑')
    config = {**identity, 'split': split, 'mode': mode, 'strategies': list(strategies),
              'score_gate': mode == 'deployment', 'threshold': threshold,
              'diagnostic_subset': mode == 'compare' and tuple(strategies) != STRATEGIES,
              'usage_note': '记录API返回的answer/judge token用量；query/embedding/rerank薄接口未暴露用量，未估算费用。',
              'latency_note': 'elapsed_seconds为检索/生成/评审，不含单列的共享Query和embedding时延；缓存行保留原执行时延并标cache_hit。',
              'judge_limitation': '生成与评审默认同一模型，存在相关偏差；需每桶至少2题及高分冲突人工抽查。'}
    summary = aggregate_results(rows)
    (output_dir / 'details.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    (output_dir / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output_dir / 'summary.md').write_text(_markdown(summary, config), encoding='utf-8')
    audit = output_dir / 'manual-audit.md'
    if not audit.exists():
        audit.write_text('# 人工抽查\n\n状态：待实际抽查，未通过人工验收。\n\n每桶至少抽查2条，并检查所有高分矛盾例。记录case ID、策略、原文、判断及理由。\n', encoding='utf-8')
    return {'rows': rows, 'summary': summary, 'config': config}


def _markdown(summary, config):
    lines = ['# ch04检索与生成评估', '', f"模式：{config['mode']}；分割：{config['split']}；语料：`{config['corpus_hash']}`。",
             '', '拒答/空回答Faithfulness=N/A；服务/评审失败单列，不填0或1。rerank的candidate Recall@50取重排前前50融合候选，最终@50不适用。',
             '', '分母及逐事实原始评审见summary.json/details.jsonl。覆盖率分母为全样本；拒答率分母为有有效终态决策的对应样本。', '']
    groups = [('全局',summary['overall'])]
    groups += [('类型 '+b, value) for b,value in summary['by_bucket'].items()]
    groups += [('难度 '+d, value) for d,value in summary['by_difficulty'].items()]
    for title, values in groups:
        lines += ['## '+title, '', '|策略|N|Recall@5|Recall@10|MRR@10|候选Recall@50|全相关命中|Faithfulness|回答覆盖|正确拒答|误拒答|事实覆盖|服务/评审失败|',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for strategy, stats in values.items():
            fields = ['recall_at_5','recall_at_10','mrr_at_10','candidate_recall_at_50','all_required_hit','faithfulness','coverage','correct_refusal_rate','false_refusal_rate','required_facts_coverage']
            numbers = ['N/A' if stats[f] is None else f'{stats[f]:.4f}' for f in fields]
            lines.append('|'+ '|'.join([strategy,str(stats['n']),*numbers, f"{stats['service_failures']}/{stats['judge_failures']}"]) +'|')
        lines.append('')
    lines += ['人工抽查见manual-audit.md，未抽查不得宣称最终质量验收。', '']
    return '\n'.join(lines)


def run_evaluation(*, split: str, mode: str, output_dir: Path, cache_dir: Path,
                   strategies=None, profile: Path | None = None) -> dict:
    selected = strategies or (('hybrid_rerank',) if mode == 'deployment' else STRATEGIES)
    return asyncio.run(_run(split=split, mode=mode, output_dir=output_dir, cache_dir=cache_dir,
                             strategies=selected, profile=profile))


def calibrate_profile(rows: list[dict], *, split: str, output_path: Path) -> dict:
    if split != 'calibration':
        raise ValueError('只允许calibration标签校准，禁止使用test')
    if not rows or any(r.get('error') for r in rows):
        raise ValueError('校准样本缺失或服务失败')
    if any(r.get('split') != 'calibration' or r.get('strategy') != 'hybrid_rerank'
           or not isinstance(r.get('can_answer'), bool) for r in rows):
        raise ValueError('只允许真实calibration split的hybrid_rerank终态行校准')
    candidates = []
    for step in range(2, 19):
        threshold = round(step * .05, 2)
        tp = fp = fn = 0
        for row in rows:
            rejected = not row['can_answer'] or row.get('max_rerank_score') is None or row['max_rerank_score'] < threshold
            tp += bool(rejected and not row['answerable'])
            fp += bool(rejected and row['answerable'])
            fn += bool(not rejected and not row['answerable'])
        positive = sum(r['answerable'] for r in rows)
        candidates.append({'threshold': threshold, 'refusal_f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
                           'false_refusal_rate': fp / positive if positive else 0,
                           'tp':tp,'fp':fp,'fn':fn,'answerable_denominator':positive,'unanswerable_denominator':len(rows)-positive})
    chosen = min(candidates, key=lambda c: (-c['refusal_f1'],c['false_refusal_rate'],abs(c['threshold']-.5),c['threshold']))
    profile = {'version':1,'identity':_identity(),'split':split,'threshold':chosen['threshold'],'candidates':candidates}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return profile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--split', choices=['calibration','test'], default='test')
    parser.add_argument('--mode', choices=['compare','deployment'], default='compare')
    parser.add_argument('--calibrate', action='store_true')
    parser.add_argument('--profile', type=Path, default=Path('log/kb/ch04-calibration.json'))
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--cache-dir', type=Path, default=Path('log/kb/ch04-eval-cache'))
    args = parser.parse_args(argv)
    try:
        if args.validate_only:
            cases = load_cases()
            from app.kb.sources import get_sources
            hashes = {s.path.as_posix(): hashlib.sha256(s.path.read_bytes()).hexdigest() for s in get_sources(corpus_version='ch04')}
            if hashes != load_eval_manifest()['source_hashes']:
                raise ValueError('语料hash变化')
            print(json.dumps({'cases':len(cases),'calibration':sum(c.split=='calibration' for c in cases),
                              'test':sum(c.split=='test' for c in cases),'database_binding':'尚未执行；真实运行时强制绑定'} ,ensure_ascii=False))
            return 0
        if args.calibrate and args.split != 'calibration':
            raise ValueError('校准只允许calibration split')
        output = args.output_dir or Path('reports/ch04') / f'{args.split}-{args.mode}'
        report = run_evaluation(split=args.split, mode=args.mode, output_dir=output, cache_dir=args.cache_dir,
                    strategies=['hybrid_rerank'] if args.calibrate else None,
                    profile=args.profile if args.mode=='deployment' else None)
        if args.calibrate:
            calibrate_profile(report['rows'], split=args.split, output_path=args.profile)
        return 1 if any(r.get('error') or r.get('judge_error') or r.get('required_facts_error') for r in report['rows']) else 0
    except Exception as exc:
        print('评估中止:'+type(exc).__name__+':请核对固定数据、迁移、profile与服务状态。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
