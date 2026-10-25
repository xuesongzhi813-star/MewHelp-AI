"""The only workflow ticket-writing path: server-owned suggestion + confirmation."""
import asyncio

from app.config import get_settings
from app.schemas import TicketActionResult
from app import tools


class ActionError(RuntimeError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def create_ticket_once(description, ticket_type, *, conversation_id):
    # Direct existing tool invocation; no generic executor timeout/retry loop.
    return tools.create_ticket.invoke({'description': description, 'ticket_type': ticket_type,
                                       'conversation_id': conversation_id})


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
                result = await asyncio.to_thread(create_once, action['description'], action['ticket_type'], conversation_id=conversation_id)
            actions[request.action_id] = {**action, 'status': 'completed', 'result': result}
            await runtime.save_actions(request.session_id, actions)
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
