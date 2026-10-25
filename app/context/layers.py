"""Pure, non-destructive views over checkpoint messages and real database anchors."""
from dataclasses import asdict
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.messages.utils import trim_messages
from app.context.contracts import HistoryView, LayeredView, MessageRef, SummaryBatch
from app.context.tokens import count_model_tokens, estimate_text


def ref_for(refs, message):
    value = refs.get(message.id)
    return asdict(value) if isinstance(value, MessageRef) else value


def paired(messages):
    """Filter incomplete tool exchanges only in the derived view, preserving user text."""
    result, index = [], 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, AIMessage) and message.tool_calls:
            ids = [c['id'] for c in message.tool_calls]
            tail = messages[index+1:index+1+len(ids)]
            if len(tail) == len(ids) and all(isinstance(t, ToolMessage) for t in tail) and {t.tool_call_id for t in tail} == set(ids):
                result.extend([message, *tail])
                index += len(tail)
        elif not isinstance(message, ToolMessage):
            result.append(message)
        index += 1
    return result


def truncate_layer2(messages):
    result, names = [], {}
    for message in paired(messages):
        if isinstance(message, AIMessage) and message.tool_calls:
            names.update({c['id']:c['name'] for c in message.tool_calls})
            result.append(message.model_copy(deep=True))
        elif isinstance(message, ToolMessage):
            result.append(message.model_copy(update={'content':f'[工具 {names.get(message.tool_call_id, message.name or "unknown")} / {message.tool_call_id} / 结果已归档]', 'artifact':None}))
        elif isinstance(message, AIMessage) and isinstance(message.content, str) and len(message.content)>60:
            result.append(message.model_copy(update={'content':message.content[:60]+'…[已截短]'}))
        else:
            result.append(message.model_copy(deep=True))
    return result


def summary_text(summaries):
    return '\n'.join(f'[summary:{s.id} 覆盖{s.from_msg_id}..{s.upto_msg_id}] {s.content}'
                     for s in sorted(summaries, key=lambda s:s.seq))


def build_layered_view(messages, *, refs, snapshot, current_turn_from_msg_id, budget, cjk_ratio=1.0):
    groups, current = {}, []
    for message in messages:
        ref = ref_for(refs, message)
        if ref is None:
            raise ValueError(f'missing DB turn reference: {message.id}')
        turn = ref['turn_from_msg_id']
        if turn == current_turn_from_msg_id:
            current.append(message)
        elif turn > (snapshot.summary_upto_msg_id or 0) and turn < current_turn_from_msg_id:
            groups.setdefault(turn, []).append(message)
    groups = {t:paired(ms) for t,ms in sorted(groups.items())}
    floor = snapshot.layer1_from_msg_id or (next(iter(groups)) if groups else current_turn_from_msg_id)
    candidate = [m for t,ms in groups.items() if t>=floor for m in ms]
    trimmed = trim_messages(candidate, max_tokens=budget.layer1, strategy='last',
                            token_counter=lambda ms:count_model_tokens(ms,cjk_ratio=cjk_ratio), allow_partial=False, start_on='human') if candidate else []
    retained = {m.id for m in trimmed}
    keep_turns = [t for t,ms in groups.items() if t>=floor and ms and all(m.id in retained for m in ms)]
    start = min(keep_turns) if keep_turns else current_turn_from_msg_id
    layer1 = [m for t,ms in groups.items() if t>=start for m in ms]
    layer2 = truncate_layer2([m for t,ms in groups.items() if t<start for m in ms])
    summaries = tuple(sorted(snapshot.summaries,key=lambda s:s.seq))
    history_messages = layer2+layer1
    lines = [summary_text(summaries)] if summaries else []
    lines += [f'[{m.id}] {m.type}: {m.content}' for m in history_messages]
    text = '\n'.join(lines)
    history = HistoryView(snapshot.conversation_id,summaries,history_messages,text,estimate_text(text,cjk_ratio=cjk_ratio))
    return LayeredView(layer1,layer2,current,start,count_model_tokens(layer2,cjk_ratio=cjk_ratio),history)


def summary_batch(view, *, messages, refs, snapshot):
    batch = [m.model_copy(deep=True) for m in messages if
             (r:=ref_for(refs,m)) and (snapshot.summary_upto_msg_id or 0)<r['turn_from_msg_id']<view.layer1_from_msg_id]
    ids = [ref_for(refs,m)['db_id'] for m in batch if ref_for(refs,m)['db_id'] is not None]
    if not ids:
        return None
    return SummaryBatch(snapshot.conversation_id,snapshot.summary_upto_msg_id,min(ids),max(ids),
                        view.layer1_from_msg_id,tuple(batch),tuple(snapshot.summaries),
                        refs={m.id:ref_for(refs,m) for m in batch})
