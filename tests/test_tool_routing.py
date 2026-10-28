"""Dynamic candidate names do not themselves grant routing or write permission."""
from app.workflow.routing import route_with_tools
from app.tooling.contracts import ToolSnapshot
from app.tooling.policy import PolicySnapshot,ToolRule


def catalog():
    metadata={'name':'query_warranty','source':'aftersales','remote_name':'query_warranty',
        'effect':'read','endpoint':'http://127.0.0.1:8022/mcp'}
    return ToolSnapshot('v','p',(metadata,),()),PolicySnapshot('p',True,
        {'aftersales':{'url':'http://127.0.0.1:8022/mcp','enabled':True}},
        {'aftersales/query_warranty':ToolRule(enabled=True,routing_intents=('售后','退款退货'))})


def test_dynamic_read_candidate_routes_only_when_local_rule_allows():
    snapshot,policy=catalog()
    info={'requested_operation':'read_tool','candidate_tool':'query_warranty'}
    assert route_with_tools('售后',info,snapshot,policy)=='business'
    assert route_with_tools('订单',info,snapshot,policy)=='business'
    assert route_with_tools('退款退货',{'requested_operation':'none'},snapshot,policy)=='refund'
    assert route_with_tools('售后',{'requested_operation':'read_tool','candidate_tool':'unknown'},snapshot,policy)=='refund'


def test_complaint_and_chat_are_not_overridden_by_remote_tool_description():
    snapshot,policy=catalog()
    info={'requested_operation':'read_tool','candidate_tool':'query_warranty'}
    assert route_with_tools('投诉',info,snapshot,policy)=='complaint'
    assert route_with_tools('闲聊',info,snapshot,policy)=='chat'


def test_only_server_verified_ticket_request_overrides_complaint():
    snapshot,policy=catalog()
    info={'requested_operation':'create_ticket'}
    assert route_with_tools('投诉',info,snapshot,policy)=='complaint'
    assert route_with_tools('投诉',{**info,'ticket_request_verified':True},snapshot,policy)=='business'

