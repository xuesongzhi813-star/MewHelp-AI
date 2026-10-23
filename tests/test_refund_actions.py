import pytest
from app.schemas import SubmitRefundRequest
from app.workflow.refund_actions import confirm_refund
from app.workflow.actions import ActionError
from tests.test_chat_actions import Runtime, Store


def setup():
    runtime=Runtime()
    runtime.actions['a'].update(kind='refund_application',order_id='1001',product='复古水洗牛仔夹克',ticket_type='售后')
    request=SubmitRefundRequest(session_id='s',action_id='a',confirmed=True,reason='尺码问题',note='希望审核')
    return runtime,request


async def test_refund_uses_server_order_and_writes_once():
    runtime,request=setup()
    writes=[]
    def create(description,ticket_type,*,conversation_id):
        writes.append((description,ticket_type,conversation_id))
        return '工单 T202610230001'
    result=await confirm_refund(request,runtime=runtime,store=Store(),create_once=create)
    again=await confirm_refund(request,runtime=runtime,store=Store(),create_once=create)
    assert result.result==again.result
    assert len(writes)==1
    assert '1001' in writes[0][0] and '尺码问题' in writes[0][0]
    assert writes[0][1:]==('售后',42)
    assert '演示' in result.result and '未执行真实退款' in result.result


@pytest.mark.parametrize('extra',[{'confirmed':False},{'reason':'任意值'},{'order_id':'9999'},{'conversation_id':666},{'note':'x'*501}])
def test_invalid_confirmation_reason_or_client_order_is_rejected(extra):
    body=dict(session_id='s',action_id='a',confirmed=True,reason='尺码问题')
    body.update(extra)
    with pytest.raises(ValueError):
        SubmitRefundRequest(**body)


async def test_uncertain_refund_never_retries():
    runtime,request=setup()
    calls=[]
    def broken(*args,**kwargs):
        calls.append(1)
        raise RuntimeError('synthetic outage')
    for _ in range(2):
        with pytest.raises(ActionError):
            await confirm_refund(request,runtime=runtime,store=Store(),create_once=broken)
    assert calls==[1]
    assert runtime.actions['a']['status']=='uncertain'
