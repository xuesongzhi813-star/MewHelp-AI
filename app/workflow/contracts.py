"""Serializable workflow contracts; models and connections stay outside graph State."""
from __future__ import annotations

import threading
import time
from typing import Literal, TypedDict, Annotated

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

Intent = Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊", "其他"]
Route = Literal["knowledge", "business", "complaint", "chat", "other", "refund"]


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)


class ControlDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_mode: Literal["answer", "clarify"] = "answer"
    actions: list[Literal["handoff", "create_ticket"]] = Field(default_factory=list, max_length=2)
    description: str = Field(default="", max_length=2000)
    ticket_type: Literal["售后", "投诉", "咨询"] = "咨询"


class BudgetState(BaseModel):
    contextual: bool = False
    token_limit: int = Field(default=12000, gt=0)
    charged_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    decision_calls: int = Field(default=0, ge=0)
    max_tool_calls: int = Field(default=6, gt=0)
    max_decisions: int = Field(default=10, gt=0)
    deadline: float = Field(default_factory=lambda: time.time() + 90, allow_inf_nan=False)
    actual_calls: int = 0
    estimated_calls: int = 0
    _lock: threading.RLock = PrivateAttr(default_factory=threading.RLock)
    _settled: set[str] = PrivateAttr(default_factory=set)


class WorkflowState(TypedDict, total=False):
    ticket_draft: dict | None
    ticket_confirmation: dict | None
    ticket_flow_closed: bool
    tool_snapshot: dict
    tool_failure: dict | None
    messages: Annotated[list[BaseMessage], add_messages]
    message_refs: dict
    summary_upto_msg_id: int | None
    layer1_from_msg_id: int | None
    summary_rows: list[dict]
    current_turn_from_msg_id: int
    history_context: dict
    context_budget: dict
    verified_reference_order_id: str | None
    session_id: str
    conversation_id: int
    request_id: str
    raw_question: str
    resolved_question: str
    intent: str
    confidence: float
    understanding: dict
    selected_order_id: str | None
    order: dict | None
    selection: dict | None
    expansion_queries: list[str]
    expansion_fallback: bool
    continuation_of: str | None
    usage_segments: list[dict]
    route: str
    retrieval: dict | None
    gate_reason: str
    budget: dict
    status: str
    pending_decision: dict | None
    actions: dict
    turn_action_ids: list[str]
    node_trace: list[str]
    final_text: str
    control: dict | None
