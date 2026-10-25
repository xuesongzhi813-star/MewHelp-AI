"""Derive history capacity from the configured window, never from row count."""
import math

from app.context.contracts import ContextBudget
from app.context.tokens import count_model_tokens


class ContextBudgetError(RuntimeError):
    def __init__(self, detail: str = ''):
        super().__init__('上下文预算不足' + ('：' + detail if detail else ''))


def derive_budget(settings, *, system_tokens: int, summary_tokens: int) -> ContextBudget:
    s = settings
    system = max(s.context_system_reserve_tokens, system_tokens)
    evidence = s.rerank_top_k * s.context_evidence_tokens_per_hit
    summary = max(s.context_summary_reserve_tokens, summary_tokens)
    safety = math.ceil(s.model_context_window * s.context_safety_ratio) + s.context_protocol_reserve_tokens
    peak = s.max_user_input_tokens + (s.max_agent_steps - 1) * (s.tool_result_max_tokens + s.agent_decision_max_tokens)
    available = s.model_context_window - system - evidence - summary - s.max_output_tokens - safety - peak
    history = max(0, min(s.context_target_turns * s.context_steady_turn_tokens, available))
    return ContextBudget(s.model_context_window, system, evidence, summary, s.max_output_tokens,
                         safety, peak, history, max(0, 7 * history // 10 - 1), 3 * history // 10)


def cumulative_turn_limit(settings) -> int:
    explicit = settings.context_turn_token_budget
    return explicit if explicit is not None else (settings.max_agent_steps + 6) * settings.model_context_window


def ensure_fits(messages, *, schemas, output_cap: int, settings) -> int:
    count = count_model_tokens(messages, schemas=schemas, cjk_ratio=settings.context_cjk_tokens_per_char)
    safety = math.ceil(settings.model_context_window * settings.context_safety_ratio) + settings.context_protocol_reserve_tokens
    if output_cap <= 0 or count + output_cap + safety > settings.model_context_window:
        raise ContextBudgetError(f'input={count} output={output_cap} safety={safety} window={settings.model_context_window}')
    return count


def validate_startup(settings, *, schemas) -> ContextBudget:
    from langchain_core.messages import SystemMessage
    from app.workflow.agent import AGENT_PROMPT
    system = count_model_tokens([SystemMessage(content=AGENT_PROMPT)], schemas=schemas,
                                cjk_ratio=settings.context_cjk_tokens_per_char)
    budget = derive_budget(settings, system_tokens=system, summary_tokens=0)
    minimum = (settings.max_user_input_tokens + min(128, settings.max_output_tokens)
               + min(128, settings.tool_result_max_tokens) + min(64, settings.agent_decision_max_tokens) + 16)
    if budget.layer1 < minimum:
        raise ContextBudgetError(f'不能容纳一轮；layer1={budget.layer1} minimum={minimum} breakdown={budget.to_dict()}')
    return budget
