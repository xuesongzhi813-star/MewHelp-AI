"""Verify request and problem text come from this conversation's actual user rows."""
from types import SimpleNamespace
import pytest
from app.workflow.ticket_draft import validate_ticket_draft


def row(id,text,*,role='user',conversation_id=42):
    return SimpleNamespace(id=id,content=text,role=role,conversation_id=conversation_id)


def request(**extra):
    return {'requested_operation':'create_ticket','request_source_id':'db:1','request_quote':'帮我建工单',
            'ticket_type':'售后','description_sources':[],**extra}


def test_explicit_request_without_problem_stays_collecting():
    draft=validate_ticket_draft(request(),user_rows=[row(1,'帮我建工单')],prior_draft=None,current_turn=1)
    assert draft and draft.status=='collecting' and 'description' in draft.missing


def test_complete_draft_is_composed_only_from_verified_quotes():
    rows=[row(1,'帮我建工单，订单1001没有收到')]
    info=request(description_sources=[{'message_id':'db:1','quote':'订单1001没有收到'}])
    draft=validate_ticket_draft(info,user_rows=rows,prior_draft=None,current_turn=1)
    assert draft.status=='ready' and draft.description=='订单1001没有收到'
    assert draft.conversation_id==42 and draft.ticket_type=='售后'


@pytest.mark.parametrize('source',[
    row(1,'帮我建工单，订单1001没有收到',role='assistant'),
    row(1,'帮我建工单，订单1001没有收到',role='tool'),
    row(1,'帮我建工单，订单1001没有收到',conversation_id=99),
])
def test_non_user_or_cross_conversation_cannot_authorize(source):
    rows=[source,row(2,'补充问题',conversation_id=42)]
    info=request(description_sources=[{'message_id':'db:1','quote':'订单1001没有收到'}])
    assert validate_ticket_draft(info,user_rows=rows,prior_draft=None,current_turn=2) is None


@pytest.mark.parametrize('text',[
    '不要帮我建工单','朋友说“帮我建工单”是什么意思','他说帮我建工单，但我不需要',
    '我不想建工单','你能解释“帮我建工单”吗',
])
def test_negation_quotation_and_third_party_requests_do_not_start_draft(text):
    info=request(request_quote='帮我建工单' if '帮我建工单' in text else text)
    assert validate_ticket_draft(info,user_rows=[row(1,text)],prior_draft=None,current_turn=1) is None


def test_invented_problem_quote_is_rejected():
    info=request(description_sources=[{'message_id':'db:1','quote':'订单9999损坏'}])
    assert validate_ticket_draft(info,user_rows=[row(1,'帮我建工单，订单1001未收到')],prior_draft=None,current_turn=1) is None


def test_only_a_current_answer_can_extend_collecting_draft():
    first=row(1,'帮我建工单')
    draft=validate_ticket_draft(request(),user_rows=[first],prior_draft=None,current_turn=1)
    second=row(2,'包裹还没到')
    info=request(description_sources=[{'message_id':'db:2','quote':'包裹还没到'}])
    complete=validate_ticket_draft(info,user_rows=[first,second],prior_draft=draft,current_turn=2)
    assert complete.status=='ready' and complete.description=='包裹还没到'
    assert validate_ticket_draft({'requested_operation':'read_tool','candidate_tool':'query_logistics'},user_rows=[first,row(2,'查物流')],prior_draft=draft,current_turn=2) is None


def test_cancel_clears_draft_even_if_model_claims_create_ticket():
    first=row(1,'帮我建工单')
    draft=validate_ticket_draft(request(),user_rows=[first],prior_draft=None,current_turn=1)
    assert validate_ticket_draft(request(),user_rows=[first,row(2,'先不建了')],prior_draft=draft,current_turn=2) is None


@pytest.mark.parametrize('description',['','  ','好','帮我建工单','字'*2001])
def test_missing_or_oversized_problem_cannot_be_ready(description):
    text='帮我建工单，'+description
    info=request(description_sources=[{'message_id':'db:1','quote':description}])
    draft=validate_ticket_draft(info,user_rows=[row(1,text)],prior_draft=None,current_turn=1)
    assert draft is None or draft.status!='ready'

