"""Explicit deterministic skeleton around the agent's decision/tool cycle."""
from dataclasses import dataclass
from typing import Any, Callable

from langgraph.graph import StateGraph, START, END

from app.workflow.contracts import WorkflowState
from app.workflow.nodes import node_bodies


@dataclass
class WorkflowDeps:
    gateway: Any
    store: Any
    retrieve: Callable
    execute: Callable
    record_low_confidence: Callable


def build_workflow(*, checkpointer, deps: WorkflowDeps):
    builder = StateGraph(WorkflowState)
    for name, body in node_bodies(deps).items():
        async def wrapped(state, _body=body, _name=name):
            result = await _body(state)
            return {**result, 'node_trace': [*state.get('node_trace', []), _name]}
        builder.add_node(name, wrapped)
    builder.add_edge(START, 'resolve_reference')
    builder.add_edge('resolve_reference', 'classify_intent')
    builder.add_conditional_edges('classify_intent', lambda state: state['route'], {
        'knowledge': 'retrieve_knowledge', 'business': 'agent_decide', 'complaint': 'complaint_reply', 'chat': 'chat_reply'})
    builder.add_edge('retrieve_knowledge', 'evidence_gate')
    builder.add_conditional_edges('evidence_gate', lambda state: bool(state['gate_reason']), {True: 'refusal_reply', False: 'agent_decide'})
    builder.add_conditional_edges('agent_decide', lambda state: 'agent_tools' if state.get('pending_decision') and state['pending_decision'].get('tool_calls') else 'agent_answer')
    builder.add_conditional_edges('agent_tools', lambda state: 'agent_answer' if state.get('control') is not None else 'agent_decide')
    for name in ('agent_answer', 'complaint_reply', 'chat_reply', 'refusal_reply'):
        builder.add_edge(name, 'log_result')
    builder.add_edge('log_result', END)
    return builder.compile(checkpointer=checkpointer)
