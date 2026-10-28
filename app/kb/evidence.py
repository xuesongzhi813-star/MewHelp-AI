"""Explainable evidence scores; they are not probabilities of correctness."""
from dataclasses import dataclass, field
import math

from app.kb.contracts import RetrievalResult


@dataclass(frozen=True)
class EvidenceFeatures:
    top1: float
    count: int
    margin: float
    single_hit: bool


@dataclass(frozen=True)
class EvidencePolicy:
    version: str
    cutoff: float
    weights: tuple[float, float, float]
    threshold: float
    calibration_hash: str
    diagnostics: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        numbers = [self.cutoff, self.threshold, *self.weights]
        if (len(self.weights) != 3 or any(type(v) not in (int, float) or not math.isfinite(v)
                or not 0 <= v <= 1 for v in numbers) or not math.isclose(sum(self.weights), 1.0)):
            raise ValueError('invalid evidence policy')


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    score: float | None
    features: EvidenceFeatures
    reason: str


def evidence_features(result: RetrievalResult, cutoff: float) -> EvidenceFeatures:
    unique = {}
    for hit in result.hits:
        score = hit.rerank_score
        if hit.score_type != 'rerank' or type(score) not in (int,float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('invalid_rerank_score')
        unique.setdefault(hit.chunk_id, float(score))
    scores = sorted(unique.values(), reverse=True)
    return EvidenceFeatures(scores[0] if scores else 0., sum(s >= cutoff for s in scores),
        scores[0]-scores[1] if len(scores)>1 else 0., len(scores)==1)


def score_evidence(result: RetrievalResult, policy: EvidencePolicy) -> GateDecision:
    empty = EvidenceFeatures(0.,0,0.,False)
    if result.strategy != 'hybrid_rerank':
        from app.kb.answering import retrieval_rejection_reason
        reason = retrieval_rejection_reason(result)
        return GateDecision(not bool(reason), None, empty, reason)
    try:
        features = evidence_features(result, policy.cutoff)
    except ValueError:
        return GateDecision(False, None, empty, 'invalid_rerank_score')
    if not result.hits:
        return GateDecision(False, 0., features, 'no_evidence')
    signals = (features.top1, min(features.count,3)/3, features.margin)
    score = sum(w*s for w,s in zip(policy.weights,signals))
    allow = score > policy.threshold
    return GateDecision(allow, score, features, '' if allow else 'evidence_below_threshold')
