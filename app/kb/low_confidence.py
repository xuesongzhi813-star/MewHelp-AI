"""低置信度问题写入；只由聊天终态调用，检索重试没有写库副作用。"""
from typing import Literal

from sqlalchemy.orm import Session

from app.db import get_session_local
from app.models import LowConfidenceQuestion


def record_low_confidence(
    raw_question: str,
    conversation_id: int | None,
    source: Literal['retrieval_low_conf', 'self_check'],
    reason: str,
    *,
    session: Session | None = None,
    identity=None,
    snapshot=None,
) -> int:
    if source not in {'retrieval_low_conf', 'self_check'}:
        raise ValueError('本章只记录检索低置信度与生成自评问题')
    if not raw_question.strip():
        raise ValueError('原话不能为空')
    from app.flywheel.store import current_event, record_event
    from app.flywheel.contracts import PoolEvent
    from app.flywheel.snapshots import snapshot_from_retrieval
    event = current_event()
    if event is not None:
        cid = event.identity.conversation_id if event.identity else event.conversation_id
        if (event.raw_question, cid, event.source, event.reason) != (raw_question, conversation_id, source, reason):
            raise ValueError('问题池上下文与本轮不一致')
        return record_event(event, session=session,
            session_factory=get_session_local() if session is None else None).id
    if identity is not None or snapshot is not None:
        event = PoolEvent(identity, source, raw_question, reason,
            snapshot if snapshot is not None else snapshot_from_retrieval(identity, None), conversation_id)
        return record_event(event, session=session,
            session_factory=get_session_local() if session is None else None).id

    def insert(target: Session) -> int:
        row = LowConfidenceQuestion(raw_question=raw_question,
                                    conversation_id=conversation_id,
                                    source=source, reason=reason)
        target.add(row)
        target.flush()
        return row.id

    if session is not None:
        return insert(session)
    with get_session_local()() as owned:
        with owned.begin():
            return insert(owned)
