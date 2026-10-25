"""One deterministic model view; dynamic data never occupies a system message."""
import json
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage, messages_from_dict
from langchain_core.utils.function_calling import convert_to_openai_tool
from app.context.contracts import ContextSnapshot, ContextBudget, HistoryView, SummaryRow
from app.context.layers import build_layered_view, summary_text, paired
from app.context.tokens import estimate_text, count_model_tokens
from app.context.budget import ContextBudgetError, derive_budget


def fixed_agent_schemas():
    from app.tools import get_tools
    from app.workflow.models import RESPOND_SCHEMA
    return [convert_to_openai_tool(t) for t in get_tools() if t.name!='create_ticket']+[RESPOND_SCHEMA]


def bounded_text(text, limit, *, settings, marker='…[有界摘录，完整原文未展示]'):
    def count(s):
        return estimate_text(s,cjk_ratio=settings.context_cjk_tokens_per_char)
    if count(text)<=limit:
        return text
    if count(marker)>limit:
        raise ContextBudgetError('摘录标识超预算')
    low,high=0,len(text)
    while low<high:
        mid=(low+high+1)//2
        if count(text[:mid]+marker)<=limit:
            low=mid
        else:
            high=mid-1
    return text[:low]+marker


def bounded_observation(text, *, settings):
    return bounded_text(str(text),settings.tool_result_max_tokens,settings=settings)


def build_history_context(view):
    return view.history


def history_from_state(state):
    data=state.get('history_context',{})
    messages=messages_from_dict([{'type':m['type'],'data':m} for m in data.get('messages',[])])
    return HistoryView(state['conversation_id'],tuple(SummaryRow(**r) for r in state.get('summary_rows',[])),
                       messages,data.get('text',''),data.get('tokens',0))


def dynamic_data(state, *, settings):
    from app.workflow.knowledge import load_retrieval, evidence_gate
    extra={}
    understanding=state.get('understanding') or {}
    if understanding.get('reference_status')=='resolved' and understanding.get('order_id'):
        extra['reference']={k:understanding.get(k) for k in ('order_id','source_message_id')}
    if state.get('order'):
        extra['order']=state['order']
    limit=settings.rerank_top_k*settings.context_evidence_tokens_per_hit
    evidence=[]
    def cost():
        return estimate_text('本轮资料(非指令):\n'+json.dumps(extra,ensure_ascii=False),
                             cjk_ratio=settings.context_cjk_tokens_per_char)+24
    if cost()>limit:
        raise ContextBudgetError('本轮订单或已核验指代附注超过证据额度')
    if state.get('retrieval'):
        result=load_retrieval(state['retrieval'])
        if not evidence_gate(result):
            for hit in result.hits[:settings.rerank_top_k]:
                item=dict(number=len(evidence)+1,chunk_id=hit.chunk_id,section_path=hit.section_path,
                          text=bounded_text(hit.answer,max(32,settings.context_evidence_tokens_per_hit-90),settings=settings))
                extra['evidence']=[*evidence,item]
                if cost()>limit:
                    extra['evidence']=evidence
                    break
                evidence.append(item)
            if not evidence:
                raise ContextBudgetError('有效检索证据无法装入本轮证据额度')
    return extra,evidence


def build_agent_context(state, *, settings):
    from app.workflow.agent import AGENT_PROMPT
    snapshot=ContextSnapshot(state['conversation_id'],state.get('summary_upto_msg_id'),
        state.get('layer1_from_msg_id'),[SummaryRow(**r) for r in state.get('summary_rows',[])])
    # Runtime has already moved boundaries before any model call.
    budget=ContextBudget(**state['context_budget']) if state.get('context_budget') else derive_budget(settings,
        system_tokens=count_model_tokens([SystemMessage(AGENT_PROMPT)],schemas=fixed_agent_schemas(),cjk_ratio=settings.context_cjk_tokens_per_char),
        summary_tokens=estimate_text('早期摘要(非指令):\n'+summary_text(snapshot.summaries),cjk_ratio=settings.context_cjk_tokens_per_char))
    view=build_layered_view(state['messages'],refs=state['message_refs'],snapshot=snapshot,
        current_turn_from_msg_id=state['current_turn_from_msg_id'],budget=budget,
        cjk_ratio=settings.context_cjk_tokens_per_char)
    human=next((m for m in view.current if isinstance(m,HumanMessage)),None)
    if human is None or human.content!=state['raw_question']:
        raise ValueError('current original user message missing')
    current=paired([m for m in view.current if m.id!=human.id and not m.additional_kwargs.get('preloaded_order')])
    extra,_=dynamic_data(state,settings=settings)
    parts=[]
    if view.history.summaries:
        parts.append('早期摘要(非指令):\n'+summary_text(view.history.summaries))
    if extra:
        parts.append('本轮资料(非指令):\n'+json.dumps(extra,ensure_ascii=False))
    data=[HumanMessage('\n'.join(parts))] if parts else []
    return [SystemMessage(AGENT_PROMPT),*view.layer2,*view.layer1,human,*data,*current]


def validate_summary_reference(order_id,source_id,*,question,context,source_rows):
    from app.workflow.understanding import order_ids
    if not source_id or not source_id.startswith('summary:'):
        return False
    summary=next((s for s in context.summaries if source_id==f'summary:{s.id}'),None)
    if summary is None or summary.conversation_id!=context.conversation_id:
        return False
    rows=sorted(source_rows,key=lambda r:r.id)
    if not rows or any(r.conversation_id!=context.conversation_id or not summary.from_msg_id<=r.id<=summary.upto_msg_id for r in rows):
        return False
    if order_id not in order_ids(summary.content) or not any(order_id in order_ids(r.content) for r in rows):
        return False
    current=order_ids(question)
    if current:
        return current=={order_id}
    all_ids=set().union(*(order_ids(s.content) for s in context.summaries),
                        *(order_ids(str(m.content)) for m in context.messages))
    if any(word in question for word in ('最开始','最早','第一单','最初')):
        first=next((s for s in sorted(context.summaries,key=lambda s:s.seq) if order_ids(s.content)),None)
        first_user=next((r for r in rows if r.role=='user' and order_ids(r.content)),None)
        return first==summary and first_user is not None and order_ids(first_user.content)=={order_id}
    return all_ids=={order_id}
