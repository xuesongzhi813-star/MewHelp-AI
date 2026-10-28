"""The only workflow ticket-writing path: server-owned suggestion + confirmation."""
import asyncio

from app.config import get_settings
from app.schemas import TicketActionResult
from app import tools


class ActionError(RuntimeError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


class _ActionRejected(ActionError):
    """The engine refused admission, so no business write was attempted."""


def create_ticket_once(description, ticket_type, *, conversation_id):
    raise ActionError('建工单需要已初始化的工具服务和服务器确认上下文。')


async def confirm_ticket(request, *, runtime, store, create_once=create_ticket_once,
                         action_kind='create_ticket', prepare_action=None):
    from app.context.identity import parse_canonical
    try:
        identity=parse_canonical(request.session_id)
        if identity:
            identity=await runtime.resolve_identity(request.session_id)
    except (ValueError,LookupError):
        raise ActionError('会话不存在。',404) from None
    async with runtime.locks.hold(request.session_id):
        state = await runtime.get_state(request.session_id)
        actions = state.get('actions', {})
        action = actions.get(request.action_id)
        if not action or action.get('kind') != action_kind:
            raise ActionError('此会话没有可确认的工单建议。', 404)
        if action['status'] == 'completed':
            return TicketActionResult(action_id=request.action_id, status='completed', result=action['result'])
        if action['status']=='rejected':
            raise ActionError(action.get('result','校验未通过，未创建工单。'),action.get('error_status_code',422))
        if action['status'] != 'available':
            raise ActionError('工单结果尚未确认，请联系客服核实，请勿重复提交。')
        conversation_id = identity.conversation_id if identity else await asyncio.to_thread(store.get_or_create_conversation, request.session_id)
        if conversation_id != state.get('conversation_id'):
            raise ActionError('会话关联已变化，请重新提出工单诉求。')
        if prepare_action is not None:
            action = prepare_action(action)
        actions[request.action_id] = {**action, 'status': 'creating'}
        # A failed pre-write checkpoint must prevent invoking the business tool.
        try:
            await runtime.save_actions(request.session_id, actions)
        except Exception:
            raise ActionError('无法保存确认状态，尚未调用建工单操作。') from None
        try:
            async with asyncio.timeout(get_settings().tool_timeout):
                service=getattr(runtime,'tool_service',None)
                if service is not None and create_once is create_ticket_once:
                    execution=await service.confirmed_action(request.session_id,conversation_id,request.action_id,
                        action['description'],action['ticket_type'])
                    if execution.status!='success':
                        if execution.outcome in {'invalid_arguments','permission_denied'}:
                            raise _ActionRejected(execution.content+' 未创建工单。',
                                422 if execution.outcome=='invalid_arguments' else 403)
                        raise ActionError(execution.content)
                    result=execution.content
                else:
                    result = await asyncio.to_thread(create_once, action['description'], action['ticket_type'], conversation_id=conversation_id)
            actions[request.action_id] = {**action, 'status': 'completed', 'result': result}
            await runtime.save_actions(request.session_id, actions)
        except _ActionRejected as exc:
            actions[request.action_id]={**action,'status':'rejected','result':str(exc),
                'error_status_code':exc.status_code}
            try:
                await runtime.save_actions(request.session_id,actions)
            except Exception:
                pass
            raise ActionError(str(exc),exc.status_code) from None
        except (Exception, asyncio.CancelledError) as exc:
            actions[request.action_id] = {**action, 'status': 'uncertain'}
            try:
                await asyncio.shield(runtime.save_actions(request.session_id, actions))
            except Exception:
                pass  # persisted creating is also non-retryable after restart
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ActionError('工单结果尚未确认，请联系客服核实，请勿重复提交。') from None
        return TicketActionResult(action_id=request.action_id, status='completed', result=result)
