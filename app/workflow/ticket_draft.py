"""Server-owned ticket drafts cite exact user text; model output cannot authorize writes."""
from __future__ import annotations
import json
import re
import hashlib
from dataclasses import dataclass,asdict,fields

_NEGATIVE=re.compile(r'(?:不要|不用|不想|不希望|不需要|没打算|无需|暂不|先不|别)[^，。！？；,;.!?]*(?:建|开|提交|登记)[^，。！？；,;.!?]*工单|不[^，。！？；,;.!?]{0,6}(?:建|开|提交|工单)|取消[^，。！？；,;.!?]*(?:工单|建单)')
_ACK=re.compile(r'^(?:好|好的|嗯|是的|是|行|可以|确认|售后|投诉|咨询|谢谢|谢谢你|多谢|感谢|收到|明白|明白了|好嘞|麻烦了|处理一下|请处理)[。！!？，,\s]*$')
_REQUEST=re.compile(r'(?:帮我|给我|请|麻烦|我要|我想|我需要|需要你|想要|要)[^，。！？；,;.!?]{0,12}(?:建|创建|建立|开|提交|登记)[^，。！？；,;.!?]{0,8}工单|^(?:建|创建|开|提交|登记)[^，。！？；,;.!?]{0,8}工单')
_THIRD_PARTY=re.compile(r'(?:朋友|别人|他|她|客户|同事)[^，。！？；,;.!?]{0,8}(?:说|让|要求)')
_MENTION=re.compile(r'如果|假如|假设|比如|例如|要是|万一|解释|这句话|这句|什么意思|含义')


@dataclass(frozen=True)
class TicketDraft:
    conversation_id:int
    request_source_id:str
    request_quote:str
    description:str
    ticket_type:str|None
    description_sources:tuple[dict,...]
    status:str
    missing:tuple[str,...]
    last_turn:int


def _get(value,key,default=None):
    return value.get(key,default) if isinstance(value,dict) else getattr(value,key,default)


def _current_id(row):
    return 'db:'+str(_get(row,'id'))


def explicit_ticket_request(text,quote):
    if not isinstance(quote,str) or not quote or quote not in text or _NEGATIVE.search(quote):
        return False
    start=text.find(quote)
    for match in _REQUEST.finditer(quote):
        before=text[:start+match.start()]
        clause=re.split(r'[，。！？；,;.!?]',before)[-1]
        full_clause=clause+re.split(r'[，。！？；,;.!?]',text[start+match.start():])[0]
        if _THIRD_PARTY.search(clause) or _NEGATIVE.search(full_clause) or _MENTION.search(full_clause):
            continue
        if (any(before.count(a)>before.count(b) for a,b in [('“','”'),('‘','’'),('「','」')])
                or before.count('"')%2 or before.count("'")%2):
            continue
        return True
    return False


def draft_reference(draft):
    data=asdict(draft) if isinstance(draft,TicketDraft) else draft
    identity={key:data[key] for key in ('conversation_id','request_source_id','description',
        'ticket_type','description_sources','last_turn')}
    return hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:32]


def _problem(quote):
    text=quote.strip()
    remainder=_REQUEST.sub('',text).strip('，。！？；,;.!? \n\r\t')
    return len(text)>=2 and bool(remainder) and not _ACK.fullmatch(remainder) and not _NEGATIVE.search(text)


def validate_ticket_draft(understanding,*,user_rows,prior_draft,current_turn):
    current=next((r for r in user_rows if str(_get(r,'id'))==str(current_turn)),None)
    if current is None or _get(current,'role')!='user':
        return None
    cid,text=_get(current,'conversation_id'),_get(current,'content','')
    if _NEGATIVE.search(text) or re.fullmatch(r'(?:算了|取消|先不用了|不弄了)[。！!\s]*',text):
        return None
    sources={_current_id(r):r for r in user_rows if _get(r,'role')=='user' and _get(r,'conversation_id')==cid}
    operation=_get(understanding,'requested_operation','none')
    if operation=='read_tool':
        return None
    prior=None
    if prior_draft is not None:
        data=asdict(prior_draft) if isinstance(prior_draft,TicketDraft) else prior_draft
        try:
            prior=TicketDraft(**{f.name:data[f.name] for f in fields(TicketDraft)})
        except (TypeError,KeyError):
            return None
        if prior.conversation_id!=cid or prior.status!='collecting':
            prior=None
    if operation=='create_ticket':
        source_id,quote=_get(understanding,'request_source_id'),_get(understanding,'request_quote')
        source=sources.get(source_id)
        if source is None or not explicit_ticket_request(_get(source,'content',''),quote):
            return None
        if prior is None and source_id!=_current_id(current):
            return None
    elif prior is not None:
        source_id,quote=prior.request_source_id,prior.request_quote
        source=sources.get(source_id)
        if source is None or not explicit_ticket_request(_get(source,'content',''),quote):
            return None
    else:
        return None
    raw_sources=_get(understanding,'description_sources',[]) or []
    type_reply=re.fullmatch(r'(售后|投诉|咨询)[。！!\s]*',text.strip()) if prior else None
    if prior is not None and _ACK.fullmatch(text):
        if type_reply is None:
            return TicketDraft(**{**asdict(prior),'last_turn':current_turn})
        raw_sources=prior.description_sources
    elif operation=='none':
        # A changed topic is not a continuation merely because a model quoted it.
        return None
    if (not raw_sources and prior is not None and operation=='create_ticket'
            and 'description' in prior.missing):
        # Collect the active form's missing slot directly from its real user
        # reply. A model need not reproduce or index a 2000-character message.
        if not _problem(text):
            return TicketDraft(**{**asdict(prior),'last_turn':current_turn})
        raw_sources=[{'message_id':_current_id(current),'whole_message':True}]
    description_sources,seen,has_current=[],set(),False
    for item in raw_sources:
        item=item.model_dump() if hasattr(item,'model_dump') else item
        message_id,problem=_get(item,'message_id'),_get(item,'quote','')
        source=sources.get(message_id)
        if _get(item,'whole_message') is True:
            if problem or source is None:
                return None
            problem=_get(source,'content','')
        if source is None or not isinstance(problem,str) or problem not in _get(source,'content',''):
            return None
        has_current=has_current or message_id==_current_id(current)
        if _problem(problem) and (message_id,problem) not in seen:
            seen.add((message_id,problem))
            description_sources.append({'message_id':message_id,'quote':problem})
    if prior is not None and source_id!=_current_id(current) and not has_current and not type_reply:
        return None
    description='；'.join(item['quote'] for item in description_sources)
    ticket_type=type_reply.group(1) if type_reply else (_get(understanding,'ticket_type') or (prior.ticket_type if prior else None))
    if ticket_type not in {'售后','投诉','咨询'}:
        ticket_type=None
    missing=[]
    if not description or len(description)>2000:
        missing.append('description' if not description else 'description_shorter')
    if not ticket_type:
        missing.append('ticket_type')
    serialized=json.dumps({'description':description,'ticket_type':ticket_type},ensure_ascii=False)
    upper=sum(1 if '\u4e00'<=c<='\u9fff' else len(c.encode('utf-8')) for c in serialized)+100
    from app.config import get_settings
    from app.context.tokens import estimate_text
    settings=get_settings()
    evidence=settings.rerank_top_k*settings.context_evidence_tokens_per_hit
    # The decision emits only draft_id; full text consumes the evidence/input
    # allowance, rather than the former literal-JSON output allowance.
    if upper>max(0,evidence-300) or estimate_text(serialized)>max(0,evidence-300):
        missing.append('description_shorter')
    return TicketDraft(cid,source_id,quote,description,ticket_type,tuple(description_sources),
                       'collecting' if missing else 'ready',tuple(dict.fromkeys(missing)),current_turn)

