"""合成证据的真实生成自评开发验证；不写会话/问题池。"""
import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.kb.answering import AnswerServiceError, generate_answer
from app.kb.contracts import Hit, QueryPlan, RetrievalResult
from app.llm import build_answer_model


async def run() -> int:
    cases = [json.loads(line) for line in Path('docs/samples/ch04-answer.jsonl').read_text(encoding='utf-8').splitlines() if line]
    model = build_answer_model(get_settings())
    rows = []
    for case in cases:
        plan = QueryPlan(raw_query=case['query'], normalized_query=case['query'], lexical_query=case['query'])
        hits = tuple(Hit(e['number'], .99, '开发样例', '开发证据原文', e['text'], '开发样例',
                         f"开发样例 > {case['id']}", score_type='rerank', rerank_score=.99)
                     for e in case['evidence'])
        start = time.monotonic()
        try:
            result = await generate_answer(plan, RetrievalResult(plan, 'hybrid_rerank', hits), model=model)
            row = {'id': case['id'], 'expected': case, 'actual': result.model_dump(),
                   'decision_matches': result.can_answer == case['can_answer'],
                   'required_facts_literal': {term: term in result.answer for term in case.get('required_facts', [])},
                   'note': 'required_facts字面命中仅辅助，等价说法与否定范围须人工逐条核对。'}
        except AnswerServiceError:
            row = {'id': case['id'], 'expected': case, 'service_error': True, 'decision_matches': False}
        row['elapsed_seconds'] = round(time.monotonic() - start, 3)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {'model': get_settings().llm_model, 'temperature': 0, 'cases': len(cases),
              'decision_matches': sum(r['decision_matches'] for r in rows), 'results': rows,
              'note': '开发标注验证，非正式Faithfulness评估；请看逐条人工语义核对。'}
    Path('docs/samples/ch04-answer-results.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"决策匹配 {report['decision_matches']}/{len(cases)}")
    return 0 if report['decision_matches'] == len(cases) else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(run()))
