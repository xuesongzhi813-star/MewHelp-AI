"""Reuse the chapter-four retriever, never its historical routing decision."""
import asyncio
import time
from dataclasses import asdict

from app.kb.answering import retrieval_rejection_reason
from app.kb.contracts import Hit, QueryPlan, RetrievalResult
from app.kb.query import build_understood_query_plan, build_query_plan
from app.workflow.budget import BudgetExceeded


async def retrieve_knowledge(question, *, gateway, budget, retrieve, already_understood=True):
    # Query rewriting is legacy synchronous code; its model adapter meters calls.
    remaining = budget.deadline - time.time()
    if remaining <= 0:
        raise BudgetExceeded('本轮时间预算用尽')
    async with asyncio.timeout(remaining):
        plan = (build_understood_query_plan(question) if already_understood else
                await asyncio.to_thread(build_query_plan, question, model=gateway.query_adapter(budget)))
        if time.time() >= budget.deadline or budget.charged_tokens >= budget.token_limit:
            raise BudgetExceeded('本轮处理预算用尽')
        return await asyncio.to_thread(retrieve, question, plan=plan)


def evidence_gate(result, policy=None):
    if policy is not None:
        from app.kb.evidence import score_evidence
        return score_evidence(result, policy).reason
    return retrieval_rejection_reason(result)


def retrieval_digest(data):
    import hashlib
    import json
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def state_evidence_allowed(state):
    if state.get('evidence_allowed') is not None:
        return state['evidence_allowed']
    return bool(state.get('retrieval')) and not evidence_gate(load_retrieval(state['retrieval']))


def dump_retrieval(result: RetrievalResult) -> dict:
    return {'plan': result.plan.model_dump(), 'strategy': result.strategy,
            'hits': [asdict(hit) for hit in result.hits],
            'candidate_ids': list(result.candidate_ids), 'reason': result.reason}


def load_retrieval(data: dict) -> RetrievalResult:
    return RetrievalResult(QueryPlan.model_validate(data['plan']), data['strategy'],
                           tuple(Hit(**hit) for hit in data['hits']), tuple(data['candidate_ids']), data['reason'])
