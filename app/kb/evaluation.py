"""固定评估标签、严格来源绑定和带分母的纯指标。没有模型或业务写入。"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.kb.contracts import RetrievalFilters
from app.kb.documents import Source, build_chunks, chunk_fingerprint
from app.kb.store import StoredChunk

EVAL_ROOT = Path('data/eval/ch04')
BUCKETS = ('specific_model', 'colloquial', 'synonym', 'metadata', 'multi_evidence', 'out_of_scope')


class ChunkRef(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    source: str
    section_path: str
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class EvalCase(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(min_length=1)
    raw_query: str = Field(min_length=1)
    bucket: Literal['specific_model','colloquial','synonym','metadata','multi_evidence','out_of_scope']
    difficulty: Literal['easy','medium','hard']
    split: Literal['calibration','test']
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    answerable: bool
    ground_truth_answer: str = Field(min_length=1)
    required_facts: list[str]
    forbidden_claims: list[str]
    relevant_chunk_refs: list[ChunkRef]

    @model_validator(mode='after')
    def check_labels(self):
        if self.answerable != bool(self.relevant_chunk_refs):
            raise ValueError('可答标签与相关知识块不一致')
        if self.answerable != bool(self.required_facts):
            raise ValueError('可答标签与required_facts不一致')
        return self


@dataclass(frozen=True)
class BoundCase:
    case: EvalCase
    relevant_ids: set[int]
    corpus_hash: str


class FactJudgment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str
    supported: bool
    evidence_numbers: list[int]
    reason: str


class FaithfulnessResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    facts: list[FactJudgment] = Field(default_factory=list)
    supported_count: int = 0
    fact_count: int = 0
    score: float | None = None
    error: str | None = None

    @model_validator(mode='after')
    def derive_score(self):
        self.fact_count = len(self.facts)
        self.supported_count = sum(f.supported for f in self.facts)
        self.score = self.supported_count / self.fact_count if self.fact_count and not self.error else None
        return self


def load_eval_manifest(root: Path = EVAL_ROOT) -> dict:
    return json.loads((root / 'split.json').read_text(encoding='utf-8'))


def corpus_digest(source_hashes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(source_hashes, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_cases(root: Path = EVAL_ROOT) -> list[EvalCase]:
    manifest = load_eval_manifest(root)
    text = (root / 'cases.jsonl').read_text(encoding='utf-8')
    if hashlib.sha256(text.encode()).hexdigest() != manifest.get('cases_sha256'):
        raise ValueError('评估标签hash变化或未冻结，须更新评估版本')
    cases = [EvalCase.model_validate_json(line) for line in text.splitlines() if line.strip()]
    if len(cases) != 96 or len({c.id for c in cases}) != 96 or len({c.raw_query for c in cases}) != 96:
        raise ValueError('评估集必须为96条不同ID/问题')
    counts = Counter((c.bucket, c.split) for c in cases)
    if any(counts[(bucket, split)] != count for bucket in BUCKETS for split, count in [('calibration',4),('test',12)]):
        raise ValueError('评估分桶/分割数量不符')
    for split in ('calibration','test'):
        if {c.id for c in cases if c.split == split} != set(manifest[split]):
            raise ValueError('固定split与标签不一致')
    if set(manifest['calibration']) & set(manifest['test']):
        raise ValueError('校准/测试集重叠')
    if corpus_digest(manifest['source_hashes']) != manifest['corpus_hash']:
        raise ValueError('语料hash清单不一致')
    return cases


def bind_cases(cases: Sequence[EvalCase], sources: Sequence[Source],
               stored: Sequence[StoredChunk]) -> list[BoundCase]:
    manifest = load_eval_manifest()
    source_hashes = {source.path.as_posix(): hashlib.sha256(source.path.read_bytes()).hexdigest() for source in sources}
    if source_hashes != manifest['source_hashes']:
        raise ValueError('评估语料hash变化或材料清单不匹配')
    canonical = defaultdict(list)
    for source in sources:
        for chunk in build_chunks(source.path.read_text(encoding='utf-8'), source):
            canonical[(source.path.as_posix(), chunk.section_path,
                       chunk_fingerprint(chunk.questions, chunk.answer))].append(chunk)
    database = defaultdict(list)
    for row in stored:
        database[(row.section_path, chunk_fingerprint(row.questions, row.answer), row.category, row.content_type)].append(row)
    bound = []
    for case in cases:
        ids = set()
        for ref in case.relevant_chunk_refs:
            if source_hashes.get(ref.source) != ref.source_hash:
                raise ValueError(f'{case.id}: 来源hash不符')
            matched = canonical[(ref.source, ref.section_path, ref.fingerprint)]
            if len(matched) != 1:
                raise ValueError(f'{case.id}: 原文绑定缺失或歧义')
            chunk = matched[0]
            rows = database[(ref.section_path, ref.fingerprint, chunk.category, chunk.content_type)]
            if len(rows) != 1 or rows[0].vectorize_status != 'done':
                raise ValueError(f'{case.id}: MySQL绑定缺失、歧义或未完成向量化')
            ids.add(rows[0].id)
        bound.append(BoundCase(case, ids, manifest['corpus_hash']))
    return bound


def retrieval_metrics(ranked_ids: Sequence[int], relevant_ids: set[int], *, k: int) -> dict:
    if k < 1:
        raise ValueError('k必须大于0')
    if not relevant_ids:
        return {'recall': None, 'mrr': None, 'all_required_hit': None, 'hit_count': 0, 'relevant_count': 0}
    top = list(ranked_ids[:k])
    hits = len(set(top) & relevant_ids)
    first = next((rank for rank, key in enumerate(top, 1) if key in relevant_ids), None)
    return {'recall': hits / len(relevant_ids), 'mrr': 1 / first if first else 0,
            'all_required_hit': relevant_ids.issubset(top), 'hit_count': hits, 'relevant_count': len(relevant_ids)}


def _mean(values):
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def _ratio(n: int, d: int):
    return n / d if d else None


def _summarize(rows: list[dict]) -> dict:
    evaluable = [r for r in rows if not (r.get('error') or r.get('service_error')) and isinstance(r.get('can_answer'), bool)]
    answerable = [r for r in evaluable if r['answerable']]
    unanswerable = [r for r in evaluable if not r['answerable']]
    answered = [r for r in evaluable if r['can_answer']]
    faithful = []
    for row in answered:
        value = row.get('faithfulness')
        if isinstance(value, dict):
            value = FaithfulnessResult.model_validate(value).score
        if not row.get('judge_error') and value is not None:
            faithful.append(value)
    result = {'n': len(rows), 'evaluable_n': len(evaluable), 'service_failures': len(rows) - len(evaluable),
              'answered_count': len(answered), 'coverage': _ratio(len(answered), len(rows)),
              'coverage_denominator': len(rows), 'faithfulness': _mean(faithful),
              'faithfulness_denominator': len(faithful), 'judge_failures': sum(bool(r.get('judge_error')) for r in rows),
              'answerable_denominator': len(answerable), 'unanswerable_denominator': len(unanswerable),
              'correct_refusal_rate': _ratio(sum(not r['can_answer'] for r in unanswerable), len(unanswerable)),
              'false_refusal_rate': _ratio(sum(not r['can_answer'] for r in answerable), len(answerable))}
    for metric in ('recall_at_5','recall_at_10','mrr_at_10','candidate_recall_at_50','all_required_hit'):
        values = [r.get(metric) for r in rows if r['answerable'] and r.get(metric) is not None]
        result[metric] = _mean(values)
        result[metric + '_denominator'] = len(values)
    result['retrieval_denominator'] = result['recall_at_5_denominator']
    fact_rows = [r for r in rows if not r.get('required_facts_error')]
    result['required_facts_denominator'] = sum(r.get('required_facts_total', 0) for r in fact_rows)
    result['required_facts_covered'] = sum(r.get('required_facts_covered', 0) for r in fact_rows)
    result['required_facts_coverage'] = _ratio(result['required_facts_covered'], result['required_facts_denominator'])
    result['required_facts_failures'] = sum(bool(r.get('required_facts_error')) for r in rows)
    return result


def aggregate_results(rows: Sequence[dict]) -> dict:
    overall, buckets, difficulties = defaultdict(list), defaultdict(lambda: defaultdict(list)), defaultdict(lambda: defaultdict(list))
    for row in rows:
        strategy = row['strategy']
        overall[strategy].append(row)
        buckets[row['bucket']][strategy].append(row)
        difficulties[row['difficulty']][strategy].append(row)
    return {'overall': {s: _summarize(rs) for s, rs in overall.items()},
            'by_bucket': {b: {s: _summarize(rs) for s, rs in strategies.items()} for b, strategies in buckets.items()},
            'by_difficulty': {d: {s: _summarize(rs) for s, rs in strategies.items()} for d, strategies in difficulties.items()}}
