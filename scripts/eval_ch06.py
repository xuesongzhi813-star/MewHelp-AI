"""Synthetic ch06 router evaluation; never use customer conversations as cases."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path


def equivalent_resolved_question(expected, actual):
    """Only an optional possessive before a parcel noun; preserve all other facts."""
    def canonical(text):
        return re.sub(r'(订单[A-Za-z0-9_-]+)的?(?=包裹|快递)', r'\1', text)
    return canonical(expected) == canonical(actual)


def summarize_rows(rows):
    total = len(rows)
    rate = lambda key: sum(bool(row.get(key)) for row in rows) / total if total else 0
    chains = {}
    exact_chains = {}
    per_class = {}
    for row in rows:
        good = bool(row.get('intent_correct') and row.get('semantic_question_correct',row.get('question_correct')) and row.get('order_correct') is not False)
        if row.get('chain_id'):
            chains[row['chain_id']] = chains.get(row['chain_id'], True) and good
            exact = bool(row.get('intent_correct') and row.get('question_correct') and row.get('order_correct') is not False)
            exact_chains[row['chain_id']] = exact_chains.get(row['chain_id'], True) and exact
        label = row.get('expected_intent')
        if label:
            counts = per_class.setdefault(label, {'correct': 0, 'total': 0})
            counts['total'] += 1
            counts['correct'] += int(bool(row.get('intent_correct')))
    parsed = sum(bool(r.get('first_parse_ok') or r.get('repaired_parse_ok')) for r in rows)
    return {'total': total, 'intent_accuracy': rate('intent_correct'),
            'question_accuracy': rate('question_correct'),
            'semantic_question_accuracy': sum(bool(r.get('semantic_question_correct',r.get('question_correct'))) for r in rows)/total if total else 0,
            'first_parse_rate': rate('first_parse_ok'),
            'parsed_after_repair_rate': parsed / total if total else 0,
            'system_fallback_count': sum(bool(r.get('system_fallback')) for r in rows),
            'service_errors': sum(bool(r.get('service_error')) for r in rows),
            'per_class': per_class, 'chains': chains, 'exact_chains':exact_chains,
            'order_checked':sum(r.get('order_correct') is not None for r in rows),
            'order_errors':sum(r.get('order_correct') is False for r in rows),
            'passed': bool(total and rate('intent_correct') >= .95
                           and all(chains.values()) and not any(r.get('service_error') for r in rows)
                           and all(r.get('intent_correct') for r in rows if r.get('expected_intent') == '其他')
                           and all(r.get('question_correct') for r in rows if r.get('expect_passthrough')))}


async def evaluate_router(cases, *, gateway_factory):
    """Evaluate a metered pipeline adapter exposing async evaluate_case(case)."""
    gateway = gateway_factory()
    rows = []
    for case in cases:
        row = dict(case)
        try:
            actual = await gateway.evaluate_case(case)
            row.update(actual=actual, intent_correct=actual.get('intent') == case['expected_intent'],
                       question_correct=actual.get('standalone_question') == case['expected_question'],
                       first_parse_ok=actual.get('first_parse_ok', False),
                       repaired_parse_ok=actual.get('repaired_parse_ok', False),
                       system_fallback=actual.get('system_fallback', False))
            row['order_correct'] = (actual.get('order_id') == case['expected_order_id']
                                    if case.get('expected_order_id') is not None else None)
            row['semantic_question_correct'] = (row['question_correct'] if case.get('expect_passthrough') else
                equivalent_resolved_question(case['expected_question'],actual.get('standalone_question','')))
        except Exception as exc:
            # Provider response text may contain credentials; retain exception class only.
            row.update(intent_correct=False, question_correct=False, service_error=type(exc).__name__)
        rows.append(row)
    return {'summary': summarize_rows(rows), 'rows': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=['dev', 'acceptance'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    import sys
    sys.path.insert(0, str(root))
    # Installed by the understanding/classification task; CLI never fabricates model results.
    from app.workflow.understanding import RouterEvaluationGateway
    cases = [json.loads(line) for line in (root / f'docs/samples/ch06-router-{args.split}.jsonl')
             .read_text(encoding='utf-8').splitlines() if line.strip()]
    report = asyncio.run(evaluate_router(cases, gateway_factory=RouterEvaluationGateway))
    report.update(provider='real', split=args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))
    return 0 if report['summary']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
