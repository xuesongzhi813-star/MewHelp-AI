"""Serializable workflow contracts; models and connections stay outside graph State."""
from __future__ import annotations

import threading
import time
from typing import Literal, TypedDict, Annotated

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

Intent = Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊"]
Route = Literal["knowledge", "business", "complaint", "chat"]


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent


class ControlDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_mode: Literal["answer", "clarify"] = "answer"
    actions: list[Literal["handoff", "create_ticket"]] = Field(default_factory=list, max_length=2)
    description: str = Field(default="", max_length=2000)
    ticket_type: Literal["售后", "投诉", "咨询"] = "咨询"


class BudgetState(BaseModel):
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
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    conversation_id: int
    request_id: str
    raw_question: str
    resolved_question: str
    intent: str
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
