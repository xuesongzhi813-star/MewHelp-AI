"""Read-only synthetic evaluation. Load configuration externally; never print secrets."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.executor import execute_tool
from app.llm import build_workflow_model
from app.workflow.agent import new_budget, run_agent_loop
from app.workflow.models import ModelGateway
from app.workflow.routing import classify_intent, route_intent


async def evaluate(suite: str, output: str):
    gateway = ModelGateway(build_workflow_model(get_settings()))
    rows = []
    for line in Path(f'docs/samples/ch05-{suite}.jsonl').read_text(encoding='utf-8').splitlines():
        case = json.loads(line)
        try:
            if suite == 'intents':
                budget = new_budget()
                intent = await classify_intent(case['question'], gateway=gateway, budget=budget)
                result = {'intent': intent, 'route': route_intent(intent), 'usage': budget.model_dump()}
                passed = intent == case['intent'] and result['route'] == case['route']
            else:
                result = await run_agent_loop(case['question'], gateway=gateway, execute=execute_tool, context={})
                result.pop('events')
                passed = result['tool_names'] == case['expected_tools'] and result['stop_reason'] == case['expected_mode']
                passed = passed and (not case.get('expected_action') or case['expected_action'] in [item['kind'] for item in result['actions']])
            rows.append({**case, 'passed': passed, 'result': result})
        except Exception as exc:
            rows.append({**case, 'passed': False, 'service_error': type(exc).__name__})
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        print(case['id'], rows[-1]['passed'], flush=True)
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['intents', 'agent'], required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    rows = asyncio.run(evaluate(args.suite, args.output))
    raise SystemExit(0 if all(row['passed'] for row in rows) else 1)
