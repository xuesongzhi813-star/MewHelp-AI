"""Explicit deterministic skeleton around the agent's decision/tool cycle."""
from dataclasses import dataclass
from typing import Any, Callable
import uuid

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
    context_store: Any = None
    tool_service: Any = None


def build_workflow(*, checkpointer, deps: WorkflowDeps):
    builder = StateGraph(WorkflowState)
    for name, body in node_bodies(deps).items():
        async def wrapped(state, _body=body, _name=name):
            from app.context.logging import context_scope
            from app.tooling.scope import tool_scope
            with context_scope(state), tool_scope(deps.tool_service,state.get('tool_snapshot'),state):
                result = await _body(state)
            if state.get('current_turn_from_msg_id'):
                turn = state['current_turn_from_msg_id']
                refs = dict(state.get('message_refs', {}))
                for message in result.get('messages', []):
                    message.id = (f'assistant:{turn}' if result.get('final_text') is not None
                                  else message.id or f'internal:{uuid.uuid4().hex}')
                    refs[message.id] = {'db_id':None, 'turn_from_msg_id':turn}
                if result.get('messages'):
                    result['message_refs'] = refs
            return {**result, 'node_trace': [*state.get('node_trace', []), _name]}
        builder.add_node(name, wrapped)
    builder.add_edge(START, 'resolve_reference')
    builder.add_edge('resolve_reference', 'classify_intent')
    builder.add_conditional_edges('classify_intent', lambda state: state['route'], {
        'knowledge': 'retrieve_knowledge', 'business': 'agent_decide', 'complaint': 'complaint_reply', 'chat': 'chat_reply', 'other': 'other_reply', 'refund':'order_context'})
    builder.add_conditional_edges('order_context', lambda state: bool(state.get('selected_order_id')),
                                  {True:'read_order', False:'await_order'})
    builder.add_edge('await_order', 'read_order')
    builder.add_conditional_edges('read_order',lambda state:'agent_decide' if state.get('tool_failure') else 'expand_queries')
    builder.add_edge('expand_queries', 'retrieve_policy')
    builder.add_edge('retrieve_policy', 'evidence_gate')
    builder.add_edge('retrieve_knowledge', 'evidence_gate')
    builder.add_conditional_edges('evidence_gate', lambda state: bool(state['gate_reason']), {True: 'refusal_reply', False: 'agent_decide'})
    builder.add_conditional_edges('agent_decide', lambda state: 'prepare_tool_call' if state.get('pending_decision') and state['pending_decision'].get('tool_calls') else 'agent_answer')
    builder.add_edge('prepare_tool_call','agent_tools')
    builder.add_conditional_edges('agent_tools', lambda state: 'prepare_ticket_write' if (state.get('ticket_confirmation') or {}).get('status')=='confirmed' else 'agent_answer' if state.get('control') is not None else 'agent_decide')
    builder.add_edge('prepare_ticket_write','execute_ticket_write')
    builder.add_edge('execute_ticket_write','agent_decide')
    for name in ('agent_answer', 'complaint_reply', 'chat_reply', 'other_reply', 'refusal_reply'):
        builder.add_edge(name, 'log_result')
    builder.add_edge('log_result', END)
    return builder.compile(checkpointer=checkpointer)
