from fastapi import APIRouter, HTTPException, Request

from app.schemas import ConfirmTicketRequest, TicketActionResult
from app.workflow.actions import confirm_ticket, ActionError

router = APIRouter(prefix='/api/chat/actions', tags=['chat-actions'])


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
