"""Feedback supplies message IDs; the server supplies the original question."""
import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.context.identity import parse_canonical
from app.flywheel.contracts import TurnIdentity, PoolEvent
from app.flywheel.snapshots import snapshot_for_turn
from app.flywheel.store import record_event


router = APIRouter(prefix='/api')
logger = logging.getLogger(__name__)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1, max_length=256)
    user_message_id: int = Field(gt=0)
    assistant_message_id: int = Field(gt=0)
    rating: Literal['down']


def get_event_recorder():
    return record_event


@router.post('/feedback')
async def post_feedback(payload: FeedbackRequest, request: Request, record=Depends(get_event_recorder)):
    runtime = request.app.state.workflow_runtime
    if runtime is None:
        raise HTTPException(503, '反馈服务尚未初始化。')
    try:
        canonical = parse_canonical(payload.session_id)
        if canonical:
            owner = await runtime.resolve_identity(payload.session_id)
            conversation_id = owner.conversation_id
        else:
            state = await runtime.get_state(payload.session_id)
            if not state:
                raise HTTPException(404, '会话不存在。')
            conversation_id = state['conversation_id']
        rows = await asyncio.to_thread(runtime.context_store.read_messages, conversation_id)
        question = next((r for r in rows if r.id == payload.user_message_id and r.role == 'user'), None)
        answer = next((r for r in rows if r.id == payload.assistant_message_id and r.role == 'assistant'
                       and r.turn_from_msg_id == payload.user_message_id), None)
        if question is None or answer is None:
            raise HTTPException(404, '被评价的消息不存在或不属于此会话。')
        # Unknown original trace stays unknown when checkpoints cannot be read.
        identity = TurnIdentity(payload.session_id, conversation_id, question.id, '', '', answer.id)
        snapshot = await snapshot_for_turn(runtime, identity)
        event = PoolEvent(snapshot.identity or identity, 'user_feedback', question.content,
                          '用户反馈未解决', snapshot)
        result = await asyncio.to_thread(record, event)
        return {'event_id':result.id, 'created':result.created, 'snapshot_status':snapshot.snapshot_status}
    except HTTPException:
        raise
    except (ValueError, LookupError):
        raise HTTPException(404, '会话或消息标识无效。') from None
    except Exception as exc:
        logger.warning('feedback persistence failed exception=%s', type(exc).__name__)
        raise HTTPException(503, '反馈暂未保存，请稍后重试。') from None
