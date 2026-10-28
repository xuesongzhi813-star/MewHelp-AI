from dataclasses import replace
import pytest

from app.kb.contracts import Hit, QueryPlan, RetrievalResult


def retrieval(scores, *, ids=None, strategy='hybrid_rerank'):
    hits = tuple(Hit((ids or list(range(1,len(scores)+1)))[i], .8, '政策', '问题', '原文',
        '文档章节', '政策', score_type='rerank', rerank_score=s) for i,s in enumerate(scores))
    return RetrievalResult(QueryPlan(raw_query='问题',normalized_query='问题',lexical_query='问题'), strategy, hits)


def policy(**changes):
    from app.kb.evidence import EvidencePolicy
    return EvidencePolicy(**{'version':'test','cutoff':.5,'weights':(.8,.1,.1),
                            'threshold':.5,'calibration_hash':'synthetic',**changes})


def test_multiple_signals_count_unique_chunks_and_keep_real_margin():
    from app.kb.evidence import score_evidence
    result = score_evidence(retrieval([.9,.85,.7],ids=[1,1,2]),policy())
    assert result.allow
    assert result.features.count == 2
    assert result.features.margin == pytest.approx(.2)
    assert result.score == pytest.approx(.8*.9 + .1*2/3 + .1*.2)


@pytest.mark.parametrize('score',[None,True,'0.9',float('nan'),float('inf'),-.01,1.01])
def test_invalid_rerank_score_is_never_confident(score):
    from app.kb.evidence import score_evidence
    result = score_evidence(retrieval([score]),policy())
    assert not result.allow and result.reason == 'invalid_rerank_score'


def test_single_hit_has_no_fabricated_second_score():
    from app.kb.evidence import score_evidence
    result = score_evidence(retrieval([.9]),policy())
    assert result.features.single_hit and result.features.margin == 0


def test_exact_threshold_refuses_and_empty_results_cannot_pass():
    from app.kb.evidence import score_evidence
    assert not score_evidence(retrieval([.5]),policy(weights=(1.,0.,0.),threshold=.5)).allow
    assert not score_evidence(retrieval([]),policy(threshold=0)).allow


def test_dense_strategy_keeps_legacy_gate_instead_of_rerank_threshold():
    from app.kb.evidence import score_evidence
    result = score_evidence(retrieval([None],strategy='dense'),policy(threshold=1.))
    assert result.allow and result.score is None
