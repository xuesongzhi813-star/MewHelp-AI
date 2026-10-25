"""Exact local model payload audit, configured independently of demo launchers."""
import hashlib
import json
import logging
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from app.context.tokens import model_payload, count_model_tokens

logger=logging.getLogger(__name__)
_scope=ContextVar('model_context_scope',default={})


def configure_context_logging(path):
    path=Path(path).resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    root=logging.getLogger('app'); root.setLevel(logging.INFO)
    if any(getattr(h,'baseFilename',None)==str(path) for h in root.handlers):
        return
    handler=logging.FileHandler(path,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    root.addHandler(handler)


@contextmanager
def context_scope(state):
    token=_scope.set(state)
    try:
        yield
    finally:
        _scope.reset(token)


def _write(kind,data):
    logger.info('%s %s',kind,json.dumps(data,ensure_ascii=False,default=str,separators=(',',':')))


def log_history_context(session_id,conversation_id,request_id,*,context,phase,payload=None):
    _write('history_ctx',dict(session_id=session_id,conversation_id=conversation_id,request_id=request_id,
        phase=phase,summaries=[{'source_id':f'summary:{s.id}','content':s.content,
        'from':s.from_msg_id,'upto':s.upto_msg_id} for s in context.summaries],
        messages=[{'id':m.id,'role':m.type,'content':m.content,'tool_calls':getattr(m,'tool_calls',[])} for m in context.messages],
        history_text=context.text,window_count=len(context.messages),tokens=context.tokens,
        payload=model_payload(payload) if payload is not None else None))


def log_model_context(session_id,conversation_id,request_id,*,messages,schemas,budget,phase):
    from app.config import get_settings
    _write('model_ctx',dict(session_id=session_id,conversation_id=conversation_id,request_id=request_id,
        phase=phase,messages=model_payload(messages),window_count=len(messages),
        tokens=count_model_tokens(messages,schemas=schemas,cjk_ratio=get_settings().context_cjk_tokens_per_char),
        budget=budget.to_dict() if hasattr(budget,'to_dict') else budget,
        schema_hash=hashlib.sha256(json.dumps(schemas,ensure_ascii=False,sort_keys=True).encode()).hexdigest()))


def log_gateway_call(messages,schemas,phase,output_cap):
    from app.context.assembly import history_from_state
    state=_scope.get()
    sid,cid,rid=(state.get(k) for k in ('session_id','conversation_id','request_id'))
    if phase in {'decide','answer'}:
        log_model_context(sid,cid,rid,messages=messages,schemas=schemas,
                          budget={**state.get('context_budget',{}),'output_cap':output_cap},phase=phase)
    elif state.get('history_context'):
        log_history_context(sid,cid,rid,context=history_from_state(state),phase=phase,payload=messages)
    else:
        _write('history_ctx',dict(session_id=sid,conversation_id=cid,request_id=rid,phase=phase,
                                 payload=model_payload(messages),window_count=len(messages),tokens=count_model_tokens(messages)))
