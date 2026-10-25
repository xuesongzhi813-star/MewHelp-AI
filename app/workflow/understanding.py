"""Multi-turn understanding contracts and boundary validation."""
from typing import Literal
import json
import re
from collections import Counter
from pydantic import BaseModel, ConfigDict, Field
from langchain_core.messages import HumanMessage, SystemMessage
from app.workflow.router_prompts import UNDERSTANDING_PROMPT


class UnderstandingResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    standalone_question: str = Field(min_length=1, max_length=32000)
    reference_status: Literal['unchanged', 'resolved', 'ambiguous']
    order_id: str | None = Field(default=None, max_length=64)
    source_message_id: str | None = None


async def understand_question(question, *, history, gateway, budget, context=None, source_lookup=None):
    if context is not None:
        history=[HumanMessage(s.content,id=f'summary:{s.id}') for s in context.summaries]+context.messages
    messages = [SystemMessage(content=UNDERSTANDING_PROMPT), HumanMessage(content=json.dumps({
        'history': context.text if context is not None else [{'id': m.id, 'role': m.type, 'content': m.content} for m in history],
        'question': question}, ensure_ascii=False))]
    result = None
    for attempt in range(2):
        try:
            result = UnderstandingResult.model_validate(await gateway.understand(messages, budget))
            break
        except ValueError:
            messages.append(HumanMessage(content='上次格式无效。只输出要求字段的JSON，订单与来源未知填null。'))
    fallback = UnderstandingResult(standalone_question=question, reference_status='ambiguous')
    if result is None or result.reference_status == 'ambiguous':
        return fallback
    if result.reference_status == 'unchanged':
        result = result.model_copy(update={'standalone_question': question})
    source = next((m for m in history if m.id and m.id == result.source_message_id), None)
    source_text = str(source.content) if source is not None else ''
    if result.order_id:
        if result.source_message_id and result.source_message_id.startswith('summary:'):
            from app.context.assembly import validate_summary_reference
            summary=next((s for s in context.summaries if f'summary:{s.id}'==result.source_message_id),None) if context else None
            rows=await source_lookup(summary.from_msg_id,summary.upto_msg_id) if summary and source_lookup else []
            if not context or not validate_summary_reference(result.order_id,result.source_message_id,
                    question=question,context=context,source_rows=rows):
                return fallback
        elif validated_order_reference(result.order_id,result.source_message_id,question=question,history=history) is None:
            return fallback
    # Rewrites may add supported entities, but never lose user-specified conditions.
    candidate = result.standalone_question
    supported_numbers = set(re.findall(r'\d+(?:\.\d+)?', question + ' ' + source_text))
    if set(re.findall(r'\d+(?:\.\d+)?', candidate)) - supported_numbers:
        return fallback
    supported_skus = set(re.findall(r'MH-[A-Za-z]{2}\d+', question + ' ' + source_text))
    if set(re.findall(r'MH-[A-Za-z]{2}\d+', candidate)) - supported_skus:
        return fallback
    if not protected_facts_preserved(question, candidate):
        return fallback
    added_ids = order_ids(candidate) - order_ids(question)
    if added_ids and (not result.order_id or added_ids != {result.order_id} or not source):
        return fallback
    return result


def order_ids(text):
    token = r'(?:ORD(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+|\d+)'
    found = set()
    pattern = r'(?:订单(?:号)?\s*(?:为|是)?\s*[:：]?\s*|["\x27]order_id["\x27]\s*:\s*["\x27])(' + token + ')'
    for match in re.finditer(pattern, text, flags=re.I):
        found.add(match.group(1).upper())
        tail = text[match.end():]
        while True:
            following = re.match(r'\s*(?:和|与|及|或|、|/)\s*(?:订单(?:号)?\s*[:：]?\s*)?(' + token + ')', tail, flags=re.I)
            if not following or re.match(r'\s*(?:元|块|天|小时|件|次|%)', tail[following.end():]):
                break
            found.add(following.group(1).upper())
            tail = tail[following.end():]
    found.update(value.upper() for value in re.findall(r'(?<![A-Za-z0-9])ORD(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]+', text, flags=re.I))
    return found


def validated_order_reference(key, source_id, *, question, history):
    """An explicit current order wins; a historical reference must be unique."""
    current = order_ids(question)
    if current and current != {key}:
        return None
    if source_id is None:
        return key if current == {key} else None
    source = next((m for m in history if m.id == source_id), None)
    if source is None or order_ids(str(source.content)) != {key}:
        return None
    if not current:
        candidates = set().union(*(order_ids(str(m.content)) for m in history))
        if candidates != {key}:
            return None
    return key


def protected_facts_preserved(raw, candidate):
    numbers = Counter(re.findall(r'\d+(?:\.\d+)?', candidate))
    if any(numbers[n] < count for n, count in Counter(re.findall(r'\d+(?:\.\d+)?', raw)).items()):
        return False
    for token in re.findall(r'MH-[A-Za-z]{2}\d+|没有|没|未|不(?:能|是|要|支持|可以)?|\d+(?:\.\d+)?\s*(?:天|小时|元|块|件|次)', raw):
        if token not in candidate:
            return False
    return True


class RouterEvaluationGateway:
    """Real provider adapter for synthetic evaluations; configuration stays local."""
    def __init__(self):
        from pathlib import Path
        from dotenv import load_dotenv
        from app.config import get_settings
        from app.llm import build_workflow_model
        from app.workflow.models import ModelGateway
        root = Path(__file__).resolve().parents[2]
        config_root = root.parents[1] if root.parent.name == '.worktrees' else root
        load_dotenv(config_root / '.env')
        get_settings.cache_clear()
        self.gateway = ModelGateway(build_workflow_model(get_settings()))
        self.model_name = get_settings().intent_model or get_settings().llm_model

    async def evaluate_case(self, case):
        from langchain_core.messages import AIMessage
        from app.workflow.contracts import BudgetState
        from app.workflow.routing import classify_intent
        from app.config import get_settings
        from app.history import trim_history
        history = [(HumanMessage if row['role'] == 'user' else AIMessage)(
            content=row['content'], id=row.get('id')) for row in case['history']]
        history = trim_history(history, get_settings().history_token_budget)
        budget = BudgetState()
        start = len(self.gateway.parse_events)
        understood = await understand_question(case['question'], history=history, gateway=self.gateway, budget=budget)
        decision = await classify_intent(understood.standalone_question, gateway=self.gateway, budget=budget)
        events = self.gateway.parse_events[start:]
        schemas = {}
        for event in events:
            schemas.setdefault(event['schema'], []).append(event['parsed'])
        parsed = len(schemas) == 2 and all(any(v) for v in schemas.values())
        first = parsed and all(v[0] for v in schemas.values())
        return {'intent': decision.intent, 'confidence': decision.confidence,
                'standalone_question': understood.standalone_question, 'order_id': understood.order_id,
                'first_parse_ok': first, 'repaired_parse_ok': parsed and not first,
                'system_fallback': not parsed, 'usage': budget.model_dump(), 'model': self.model_name,
                'json_validation':'strict_raw_json'}
