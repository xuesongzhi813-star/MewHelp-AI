import json
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse

from app.schemas import ConfirmTicketRequest, TicketActionResult
from app.workflow.actions import confirm_ticket, ActionError
from app.schemas import SelectOrderRequest, SubmitRefundRequest
from app.workflow.refund_actions import confirm_refund
from app.dependencies import get_chat_model

router = APIRouter(prefix='/api/chat/actions', tags=['chat-actions'])


@router.post('/refund', response_model=TicketActionResult)
async def refund(request: SubmitRefundRequest, http_request: Request):
    runtime = getattr(http_request.app.state, 'workflow_runtime', None)
    if runtime is None:
        raise HTTPException(503, '工作流尚未初始化。')
    try:
        return await confirm_refund(request, runtime=runtime, store=runtime.store)
    except ActionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except Exception:
        raise HTTPException(503, '申请结果待核实，请联系客服，请勿重复提交。') from None


@router.get('/state')
async def state(session_id: str, http_request: Request):
    runtime = getattr(http_request.app.state, 'workflow_runtime', None)
    if runtime is None:
        raise HTTPException(503, '工作流尚未初始化。')
    try:
        return await runtime.public_session_state(session_id)
    except (ValueError,LookupError):
        raise HTTPException(404,'会话不存在。') from None
    except Exception:
        raise HTTPException(503,'会话状态暂时不可用。') from None


@router.post('/order-selection')
async def select_order(request: SelectOrderRequest, http_request: Request, model=Depends(get_chat_model)):
    runtime = getattr(http_request.app.state, 'workflow_runtime', None)
    if runtime is None:
        raise HTTPException(503, '工作流尚未初始化。')
    try:
        await runtime.validate_order_selection(request)
    except (ValueError,LookupError):
        raise HTTPException(404,'会话不存在。') from None
    except ActionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    async def events():
        async for event in runtime.stream_order_selection(request, model=model):
            yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
    return StreamingResponse(events(), media_type='text/event-stream')


@router.post('/ticket', response_model=TicketActionResult)
async def ticket(request: ConfirmTicketRequest, http_request: Request):
    runtime = getattr(http_request.app.state, 'workflow_runtime', None)
    if runtime is None:
        raise HTTPException(503, '工作流尚未初始化。')
    try:
        return await confirm_ticket(request, runtime=runtime, store=runtime.store)
    except ActionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    except Exception:
        raise HTTPException(503, '工单确认服务暂时不可用，请稍后核实。') from None
