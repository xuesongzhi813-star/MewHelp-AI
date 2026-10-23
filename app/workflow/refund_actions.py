"""User-confirmed synthetic refund application; never refunds money."""
from app.workflow.actions import create_ticket_once, confirm_ticket, ActionError


async def confirm_refund(request, *, runtime, store, create_once=create_ticket_once):
    def prepare(action):
        if not action.get('order_id'):
            raise ActionError('此申请缺少服务器关联订单，请重新发起。')
        description = (f"演示退款申请（待审核，不执行真实退款）\n订单：{action['order_id']}\n"
                       f"商品：{action.get('product', '')}\n原因：{request.reason}\n"
                       f"补充说明：{request.note}\n原问题：{action['description']}")
        return {**action, 'description':description, 'ticket_type':'售后',
                'reason':request.reason, 'note':request.note}
    result = await confirm_ticket(request, runtime=runtime, store=store, create_once=create_once,
                                  action_kind='refund_application', prepare_action=prepare)
    return result.model_copy(update={'result':'演示申请已提交，待审核；未执行真实退款。' + result.result})
