"""Frozen small-grid selection from exactly 24 calibration examples."""
from dataclasses import dataclass, asdict
import hashlib
import json
import math

from app.kb.contracts import RetrievalResult
from app.kb.evidence import EvidencePolicy, evidence_features, score_evidence


WEIGHTS = ((.8,.1,.1),(.7,.2,.1),(.7,.1,.2),(.6,.2,.2))
ALGORITHM_VERSION = 'evidence-v1'


@dataclass(frozen=True)
class CalibrationCase:
    id: str
    split: str
    answerable: bool
    relevant_ids: frozenset[int]


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _quantile(ordered, fraction):
    position = (len(ordered)-1)*fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper]-ordered[lower])*(position-lower)


def calibrate(cases: list[CalibrationCase], results: dict[str,RetrievalResult], *, identity: dict) -> EvidencePolicy:
    if len(cases) != 24 or len({c.id for c in cases}) != 24 or any(c.split != 'calibration' for c in cases):
        raise ValueError('exactly 24 distinct calibration cases required')
    if {c.answerable for c in cases} != {True,False} or any(c.id not in results for c in cases):
        raise ValueError('calibration requires both labels and all results')
    scores = []
    evidence = []
    for case in cases:
        result = results[case.id]
        if result.strategy != 'hybrid_rerank':
            raise ValueError('calibration requires hybrid_rerank results')
        evidence_features(result, 0.)  # Invalid provider data is an error, never a negative label.
        scores.extend(float(h.rerank_score) for h in result.hits)
        evidence.append({'id':case.id,'answerable':case.answerable,'relevant_ids':sorted(case.relevant_ids),
                         'hits':[{'id':h.chunk_id,'score':h.rerank_score} for h in result.hits]})
    if not scores:
        raise ValueError('no valid rerank samples to calibrate')
    ordered = sorted(scores)
    cutoffs = sorted({_quantile(ordered, q) for q in (0.,.25,.5,.75,1.)})
    calibration_hash = _digest({'algorithm':ALGORITHM_VERSION,'identity_hash':_digest(identity),
                                'samples':evidence, 'weights':WEIGHTS})
    candidates = []
    best = None
    selected = None
    for index, weights in enumerate(WEIGHTS):
        for cutoff in cutoffs:
            base = EvidencePolicy(ALGORITHM_VERSION,cutoff,weights,0.,calibration_hash)
            values = sorted({score_evidence(results[c.id],base).score for c in cases})
            thresholds = sorted({0.,1.,*((a+b)/2 for a,b in zip(values,values[1:]))})
            for threshold in thresholds:
                policy = EvidencePolicy(ALGORITHM_VERSION,cutoff,weights,threshold,calibration_hash)
                decisions = [score_evidence(results[c.id],policy).allow for c in cases]
                fp = sum(allow and not case.answerable for case,allow in zip(cases,decisions))
                fn = sum(not allow and case.answerable for case,allow in zip(cases,decisions))
                stats = {'weights':list(weights),'cutoff':cutoff,'threshold':threshold,
                         'false_positive':fp,'false_negative':fn}
                candidates.append(stats)
                key = (fp,fn,index,cutoff,threshold)
                if best is None or key < best:
                    best, selected = key, (policy,stats)
    policy, stats = selected
    samples = []
    for case in cases:
        decision = score_evidence(results[case.id],policy)
        samples.append({'id':case.id,'answerable':case.answerable,'features':asdict(decision.features),
                        'score':decision.score,'allow':decision.allow})
    return EvidencePolicy(ALGORITHM_VERSION+':'+calibration_hash[:12],policy.cutoff,policy.weights,
        policy.threshold,calibration_hash,{'selected':stats,'candidates':candidates,'samples':samples})
