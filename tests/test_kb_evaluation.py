import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.kb import evaluation as ev
from app.kb.documents import build_chunks
from app.kb.sources import get_sources
from tests.test_kb_retriever import _stored


def test_metrics_recall_mrr_and_denominators():
    assert ev.retrieval_metrics([9, 2, 4], {2, 4}, k=1)['recall'] == 0
    assert ev.retrieval_metrics([9, 2, 4], {2, 4}, k=2)['recall'] == .5
    metric = ev.retrieval_metrics([9, 2, 4], {2, 4}, k=3)
    assert metric['recall'] == 1 and metric['mrr'] == .5
    assert metric['all_required_hit'] is True


def test_empty_relevance_is_na_and_duplicate_ids_do_not_count_twice():
    assert ev.retrieval_metrics([1, 2], set(), k=10)['recall'] is None
    assert ev.retrieval_metrics([1, 2], set(), k=10)['mrr'] is None
    assert ev.retrieval_metrics([2, 2, 4], {2, 4}, k=2)['recall'] == .5


def corpus():
    sources = get_sources(corpus_version='ch04')
    rows = []
    for source in sources:
        for chunk in build_chunks(source.path.read_text(encoding='utf-8'), source):
            rows.append(_stored(len(rows) + 1, category=chunk.category, questions=chunk.questions,
                                answer=chunk.answer, section_path=chunk.section_path, content_type=chunk.content_type))
    return sources, rows


def test_dataset_fixed_split_exact_labels_and_bindings():
    cases = ev.load_cases()
    assert len(cases) == 96 and len({c.id for c in cases}) == 96
    assert sum(c.split == 'calibration' for c in cases) == 24
    assert sum(c.split == 'test' for c in cases) == 72
    for bucket in ev.BUCKETS:
        rows = [c for c in cases if c.bucket == bucket]
        assert len(rows) == 16
        assert sum(c.split == 'calibration' for c in rows) == 4
    sources, stored = corpus()
    bound = ev.bind_cases(cases, sources, stored)
    assert len(bound) == 96 and len({b.corpus_hash for b in bound}) == 1
    assert all(bool(b.relevant_ids) == b.case.answerable for b in bound)
    dev = {json.loads(line)['raw_query'] for line in Path('docs/samples/ch04-query.jsonl').read_text(encoding='utf-8').splitlines()}
    assert all(c.raw_query not in dev for c in cases if c.split == 'test')


def test_binding_missing_or_ambiguous_stored_source_is_fatal():
    cases = ev.load_cases()[:1]
    sources, stored = corpus()
    matched = ev.bind_cases(cases, sources, stored)[0].relevant_ids
    with pytest.raises(ValueError, match='绑定'):
        ev.bind_cases(cases, sources, [r for r in stored if r.id not in matched])
    row = next(r for r in stored if r.id in matched)
    with pytest.raises(ValueError, match='绑定'):
        ev.bind_cases(cases, sources, [*stored, replace(row, id=999999)])


def test_source_hash_or_section_change_fails_binding():
    cases = ev.load_cases()[:1]
    sources, stored = corpus()
    ref = cases[0].relevant_chunk_refs[0].model_copy(update={'source_hash': '0' * 64})
    with pytest.raises(ValueError, match='hash'):
        ev.bind_cases([cases[0].model_copy(update={'relevant_chunk_refs': [ref]})], sources, stored)
    ref = cases[0].relevant_chunk_refs[0].model_copy(update={'section_path': '不存在章节'})
    with pytest.raises(ValueError, match='绑定'):
        ev.bind_cases([cases[0].model_copy(update={'relevant_chunk_refs': [ref]})], sources, stored)


def test_faithfulness_derived_from_facts_not_self_reported_score():
    result = ev.FaithfulnessResult(facts=[
        ev.FactJudgment(text='有依据', supported=True, evidence_numbers=[1], reason='原文支持'),
        ev.FactJudgment(text='猜测', supported=False, evidence_numbers=[], reason='原文没有'),
    ], score=1, supported_count=2, fact_count=999)
    assert result.score == .5 and result.supported_count == 1 and result.fact_count == 2
    assert ev.FaithfulnessResult().score is None
    assert ev.FaithfulnessResult(facts=result.facts, error='judge失败').score is None


def test_aggregate_never_rewards_all_refusals_and_reports_denominators():
    rows = [
        {'strategy':'dense','bucket':'specific_model','difficulty':'easy','answerable':True,
         'can_answer':False,'recall_at_5':1.,'recall_at_10':1.,'mrr_at_10':.5,'faithfulness':None},
        {'strategy':'dense','bucket':'out_of_scope','difficulty':'hard','answerable':False,
         'can_answer':False,'recall_at_5':None,'recall_at_10':None,'mrr_at_10':None,'faithfulness':None},
    ]
    summary = ev.aggregate_results(rows)['overall']['dense']
    assert summary['n'] == 2 and summary['coverage'] == 0
    assert summary['faithfulness'] is None and summary['faithfulness_denominator'] == 0
    assert summary['recall_at_5'] == 1 and summary['retrieval_denominator'] == 1
    assert summary['correct_refusal_rate'] == 1 and summary['unanswerable_denominator'] == 1
    assert summary['false_refusal_rate'] == 1 and summary['answerable_denominator'] == 1


def test_judge_failure_is_separate_from_zero_and_refusal():
    rows = [{'strategy':'hybrid','bucket':'specific_model','difficulty':'easy','answerable':True,
             'can_answer':True,'faithfulness':None,'judge_error':'timeout'}]
    summary = ev.aggregate_results(rows)['overall']['hybrid']
    assert summary['judge_failures'] == 1 and summary['faithfulness'] is None
    assert summary['coverage'] == 1 and summary['false_refusal_rate'] == 0


def test_service_failure_does_not_count_as_successful_refusal():
    rows = [{'strategy':'bm25','bucket':'out_of_scope','difficulty':'hard','answerable':False,
             'can_answer':False,'error':'provider failure','faithfulness':None}]
    summary = ev.aggregate_results(rows)['overall']['bm25']
    assert summary['service_failures'] == 1 and summary['evaluable_n'] == 0
    assert summary['unanswerable_denominator'] == 0 and summary['correct_refusal_rate'] is None


def test_data_tampering_fails_even_when_row_count_is_unchanged(tmp_path):
    import shutil
    shutil.copy(ev.EVAL_ROOT / 'cases.jsonl', tmp_path / 'cases.jsonl')
    shutil.copy(ev.EVAL_ROOT / 'split.json', tmp_path / 'split.json')
    path = tmp_path / 'cases.jsonl'
    path.write_text(path.read_text(encoding='utf-8').replace('110厘米', '111厘米', 1), encoding='utf-8')
    with pytest.raises(ValueError, match='hash'):
        ev.load_cases(tmp_path)
