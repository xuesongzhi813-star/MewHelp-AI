"""真实单轮改写开发样例验证；仅发合成问题，不写业务库。"""
import json
import time
from pathlib import Path

from app.config import get_settings
from app.kb.query import build_query_plan, normalize_query
from app.llm import build_query_model


def main() -> int:
    source = Path('docs/samples/ch04-query.jsonl')
    cases = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line]
    model = build_query_model(get_settings())
    results = []
    for case in cases:
        start = time.monotonic()
        plan = build_query_plan(case['raw_query'], model=model)
        checks = {
            'preserve': all(normalize_query(term) in plan.normalized_query for term in case['preserve']),
            'intent': plan.intent == case['intent'],
            'filters': all(plan.filters.model_dump().get(k) == v for k, v in case['filters'].items()),
            'service_ok': not any(term in plan.rewrite_reason for term in ('服务或结构异常', '超时')),
        }
        row = {'id': case['id'], 'expected': case, 'actual': plan.model_dump(),
               'checks': checks, 'passed': all(checks.values()),
               'elapsed_seconds': round(time.monotonic() - start, 3)}
        results.append(row)
        print(json.dumps({'id': row['id'], 'passed': row['passed'],
                          'normalized': plan.normalized_query, 'intent': plan.intent,
                          'fallback': plan.rewrite_fallback}, ensure_ascii=False), flush=True)
    report = {'model': get_settings().llm_model, 'temperature': 0,
              'cases': len(cases), 'passed': sum(r['passed'] for r in results),
              'fallback_count': sum(r['actual']['rewrite_fallback'] for r in results),
              'note': '开发标注验证，非正式保留测试；标准问法语义仍需逐条人工核对。',
              'results': results}
    output = Path('docs/samples/ch04-query-results.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"{report['passed']}/{len(cases)}；结果：{output}")
    return 0 if report['passed'] == len(cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
