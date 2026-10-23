"""Read-only comparison on 12 existing frozen policy test cases; no new labels."""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


async def evaluate():
    from scripts.demo_ch06 import configure
    configure(require_profile=True)
    from scripts.eval_retrieval import _load_bound_cases
    from app.config import get_settings
    from app.llm import build_workflow_model
    from app.workflow.models import ModelGateway
    from app.workflow.agent import new_budget
    from app.kb.query import build_understood_query_plan
    from app.kb.multi_query import expand_policy_queries,retrieve_policy_queries
    from app.kb.retriever import retrieve_result
    from app.kb.reranker import rerank
    from app.workflow.knowledge import evidence_gate
    bound=[item for item in _load_bound_cases('test') if item.case.answerable and any(
        'returns-policy' in ref.source or 'after-sales-manual' in ref.source for ref in item.case.relevant_chunk_refs)][:12]
    if len(bound)!=12:
        raise ValueError('固定集核心政策样例不足12条')
    gateway=ModelGateway(build_workflow_model(get_settings()))
    path=ROOT/'reports/ch06/policy-comparison.json'
    rows=[]
    for item in bound:
        case=item.case
        row={'id':case.id,'question':case.raw_query,'relevant_ids':sorted(item.relevant_ids)}
        try:
            plan=build_understood_query_plan(case.raw_query).model_copy(update={'filters':case.filters})
            budget=new_budget()
            started=time.monotonic()
            queries=await expand_policy_queries(case.raw_query,order={'order_id':'EVAL'},gateway=gateway,budget=budget)
            row.update(queries=queries,expansion_fallback=len(queries)==1,expansion_seconds=time.monotonic()-started,
                       expansion_llm_usage=budget.model_dump())
            for label,selected in [('single_policy_query',[case.raw_query]),('expanded_policy_queries',queries)]:
                started=time.monotonic()
                async with asyncio.timeout(90):
                    result=await asyncio.to_thread(retrieve_policy_queries,case.raw_query,selected,plan=plan,
                                                  retrieve=retrieve_result,rerank=rerank)
                ids=[hit.chunk_id for hit in result.hits]
                row[label]={'hit_ids':ids,'candidate_ids':list(result.candidate_ids),
                    'recall_at_10':len(set(ids)&item.relevant_ids)/len(item.relevant_ids),
                    'all_required_hit':item.relevant_ids.issubset(ids),'gate_reason':evidence_gate(result),
                    'max_rerank_score':result.hits[0].rerank_score if result.hits else None,
                    'seconds':time.monotonic()-started}
        except Exception as exc:
            row['error']=type(exc).__name__
        rows.append(row)
        report={'complete':len(rows)==12,'threshold':get_settings().rerank_min_score,
                'usage_scope':'expansion LLM only; embeddings/rerank not token-priced here','rows':rows}
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(case.id,'error' if row.get('error') else 'done',flush=True)
    return 1 if any(row.get('error') for row in rows) else 0


if __name__=='__main__':
    raise SystemExit(asyncio.run(evaluate()))
