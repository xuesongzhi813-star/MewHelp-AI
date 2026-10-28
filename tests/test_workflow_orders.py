import importlib
import json

import app.demo_orders as demo
from app.tools import query_order
from app.mcp_servers.common import MockBusinessData
from app.workflow.orders import verified_order_id
from langchain_core.messages import HumanMessage


def test_demo_order_survives_reload_and_caller_mutation():
    first = demo.get_demo_order('1001')
    assert first is not None
    assert first['product'] == '复古水洗牛仔夹克'
    first['amount'] = -1
    assert demo.get_demo_order('1001')['amount'] == 199.0
    assert importlib.reload(demo).get_demo_order('1001')['amount'] == 199.0


def test_cards_and_tools_share_order_and_shipping_state():
    cards = demo.list_demo_orders()
    assert {c['order_id'] for c in cards} == {'1001', '1002', '1003'}
    for card in cards:
        order = json.loads(query_order.invoke({'order_id': card['order_id']}))
        assert order['product'] == card['product']
        assert order['status'] == card['status']
        assert order['amount'] == card['amount']
        assert order['is_demo'] is True
    logistics=MockBusinessData(seed=0)
    assert logistics.logistics('1001')['current_status']=='delivered'
    assert logistics.logistics('1002')['current_status'] in {'picked_up','transit','out_for_delivery'}


def test_unknown_id_is_not_in_demo_customer_list():
    assert demo.get_demo_order('9999') is None


def test_source_with_two_orders_does_not_verify_single_order():
    result = {'order_id': '1001', 'source_message_id': 'both', 'reference_status': 'resolved'}
    assert verified_order_id(result, messages=[HumanMessage(content='订单1001和订单1002', id='both')]) is None


def test_client_selection_string_is_not_proof():
    assert verified_order_id({}, messages=[], selected_order_id='1001') is None
