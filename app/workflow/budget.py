"""Reserve before a call; settle once with actual usage or a labelled estimate."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from langchain_core.messages import BaseMessage

from app.history import estimate_tokens
from app.workflow.contracts import BudgetState


class BudgetExceeded(RuntimeError):
    """No further model or tool calls may start for this turn."""


@dataclass(frozen=True)
class CallReservation:
    id: str
    input_tokens: int
    output_cap: int


def reserve_call(budget: BudgetState, messages: list[BaseMessage], *, schema_text: str, output_cap: int) -> CallReservation:
    # Full message serialization includes function names, IDs and tool arguments.
    payload = json.dumps([message.model_dump() for message in messages], ensure_ascii=False, default=str)
    input_tokens = estimate_tokens(payload + schema_text) + 16
    with budget._lock:
        remaining = budget.token_limit - budget.charged_tokens - input_tokens
        if time.time() >= budget.deadline or remaining <= 0 or output_cap <= 0:
            raise BudgetExceeded("本轮处理预算已用尽，请缩小问题范围后重试。")
        reservation = CallReservation(uuid.uuid4().hex, input_tokens, min(output_cap, remaining))
        budget.charged_tokens += input_tokens + reservation.output_cap
        return reservation


def settle_call(budget: BudgetState, reservation: CallReservation, *, usage: dict | None, output_text: str) -> None:
    actual = None
    if isinstance(usage, dict):
        counts = [usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")]
        if all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts):
            if counts[2] == counts[0] + counts[1]:
                actual = counts[2]
    with budget._lock:
        if reservation.id in budget._settled:
            raise ValueError("调用预算不可重复结算")
        budget._settled.add(reservation.id)
        charge = actual if actual is not None else reservation.input_tokens + estimate_tokens(output_text)
        budget.charged_tokens += charge - reservation.input_tokens - reservation.output_cap
        if actual is None:
            budget.estimated_calls += 1
        else:
            budget.actual_calls += 1
