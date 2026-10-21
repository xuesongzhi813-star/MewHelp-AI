"""MySQL 知识仓储:knowledge_chunks 与 qa_extraction_staging 的读写封装。

设计约定:
- 所有函数都接受可选 ``session``。传入时只 flush/execute,绝不 commit
  (事务边界归调用方);为 None 时自建会话并在退出时提交。
- 表结构由本机既有 DDL 维护,本模块只映射与读写,不做任何 DDL。
- 指纹与归一化复用 :mod:`app.kb.documents`,保证与切块/挖掘口径一致。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db import get_session_local
from app.kb.documents import chunk_fingerprint, normalize_text
from app.models import Conversation, KnowledgeChunk, Message, QaExtractionStaging


@dataclass(frozen=True)
class ChunkCandidate:
    """待写入 knowledge_chunks 的一条知识切片。"""

    category: str
    questions: str
    answer: str
    section_path: str
    content_type: str
    is_key_clause: bool


@dataclass(frozen=True)
class PendingChunk:
    """待向量化的切片(只取向量化文本需要的字段)。"""

    id: int
    category: str
    questions: str
    answer: str


@dataclass(frozen=True)
class StoredChunk:
    """已落库切片的只读快照,供页面/一致性核对使用。"""

    id: int
    category: str
    questions: str
    answer: str
    section_path: str | None
    content_type: str | None
    is_key_clause: bool
    vectorize_status: str
    vector_id: str | None
    prev_chunk_id: int | None
    next_chunk_id: int | None
    # 追加在末尾:既有按位置构造的调用方仍然兼容。
    created_at: datetime | None = None


@dataclass(frozen=True)
class StagingCandidate:
    """待写入 qa_extraction_staging 的一条挖掘结果。"""

    batch_no: str
    source_ref: str | None
    question: str
    answer: str


@dataclass(frozen=True)
class StagingRow:
    """暂存表一行的只读快照。"""

    id: int
    batch_no: str
    source_ref: str | None
    question: str
    answer: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class ConversationForMining:
    """可供挖掘的会话:turn 为 (message_id, role, content),按 message_id 升序。"""

    id: int
    user_id: str
    max_message_id: int
    turns: tuple[tuple[int, str, str], ...]


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    """传入会话时原样复用(不提交);否则自建会话并在退出时提交。"""
    if session is not None:
        yield session
        return
    owned = get_session_local()()
    try:
        with owned.begin():
            yield owned
    finally:
        owned.close()


def _to_stored(row: KnowledgeChunk) -> StoredChunk:
    return StoredChunk(
        id=row.id,
        category=row.category,
        questions=row.questions,
        answer=row.answer,
        section_path=row.section_path,
        content_type=row.content_type,
        is_key_clause=bool(row.is_key_clause),
        vectorize_status=row.vectorize_status,
        vector_id=row.vector_id,
        prev_chunk_id=row.prev_chunk_id,
        next_chunk_id=row.next_chunk_id,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# knowledge_chunks
# ---------------------------------------------------------------------------


def insert_chunks(
    candidates: Iterable[ChunkCandidate], session: Session | None = None
) -> list[int]:
    """按输入顺序插入切片并返回自增 id;vectorize_status 用数据库默认 pending。"""
    rows = [
        KnowledgeChunk(
            category=candidate.category,
            questions=candidate.questions,
            answer=candidate.answer,
            section_path=candidate.section_path,
            content_type=candidate.content_type,
            is_key_clause=candidate.is_key_clause,
        )
        for candidate in candidates
    ]
    if not rows:
        return []
    with _session_scope(session) as active:
        active.add_all(rows)
        active.flush()  # 拿到自增 id;提交由事务边界负责
        return [row.id for row in rows]


def link_siblings(ids: Sequence[int], session: Session | None = None) -> None:
    """按传入顺序串联前后指针:第 i 条 next=i+1,第 i+1 条 prev=i;首尾为 NULL。"""
    ids = list(ids)
    if not ids:
        return
    with _session_scope(session) as active:
        for index, chunk_id in enumerate(ids):
            active.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == chunk_id)
                .values(
                    prev_chunk_id=ids[index - 1] if index > 0 else None,
                    next_chunk_id=ids[index + 1] if index < len(ids) - 1 else None,
                )
            )


def iter_pending(limit: int = 32, session: Session | None = None) -> list[PendingChunk]:
    """按 id 升序取待向量化切片,最多 limit 条。"""
    with _session_scope(session) as active:
        rows = active.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.vectorize_status == "pending")
            .order_by(KnowledgeChunk.id)
            .limit(limit)
        ).all()
        return [
            PendingChunk(
                id=row.id,
                category=row.category,
                questions=row.questions,
                answer=row.answer,
            )
            for row in rows
        ]


def mark_done(ids: Sequence[int], session: Session | None = None) -> None:
    """把切片标记为已向量化,并写入 vector_id=str(id)。"""
    ids = list(ids)
    if not ids:
        return
    with _session_scope(session) as active:
        for chunk_id in ids:
            active.execute(
                update(KnowledgeChunk)
                .where(KnowledgeChunk.id == chunk_id)
                .values(vector_id=str(chunk_id), vectorize_status="done")
            )


def fetch_by_ids(
    ids: Sequence[int], session: Session | None = None
) -> dict[int, StoredChunk]:
    """按 id 批量取切片快照,返回 {id: StoredChunk};缺失的 id 不出现在结果里。"""
    ids = list(ids)
    if not ids:
        return {}
    with _session_scope(session) as active:
        rows = active.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids))
        ).all()
        return {row.id: _to_stored(row) for row in rows}


def existing_fingerprints(session: Session | None = None) -> set[str]:
    """全表「问法 + 正文」指纹集合,用于录入判重。"""
    with _session_scope(session) as active:
        rows = active.execute(
            select(KnowledgeChunk.questions, KnowledgeChunk.answer)
        ).all()
        return {
            chunk_fingerprint(questions, answer) for questions, answer in rows
        }


def all_chunk_ids(session: Session | None = None) -> list[int]:
    with _session_scope(session) as active:
        return list(active.scalars(select(KnowledgeChunk.id).order_by(KnowledgeChunk.id)))


def iter_chunks_by_ids(ids: Sequence[int]) -> Iterator[StoredChunk]:
    """有界批量回表，不受 Milvus query limit 限制。"""
    ordered = sorted(set(ids))
    for start in range(0, len(ordered), 500):
        rows = fetch_by_ids(ordered[start:start + 500])
        yield from (rows[key] for key in sorted(rows))


def mark_pending(ids: Sequence[int], session: Session | None = None) -> None:
    if not ids:
        return
    with _session_scope(session) as active:
        active.execute(update(KnowledgeChunk).where(KnowledgeChunk.id.in_(list(ids)))
                       .values(vectorize_status="pending", vector_id=None))


def existing_questions_normalized(session: Session | None = None) -> set[str]:
    """全表归一化问法集合,供对话挖掘精确去重。"""
    with _session_scope(session) as active:
        questions = active.scalars(select(KnowledgeChunk.questions)).all()
        return {normalize_text(question) for question in questions}


def done_ids(session: Session | None = None) -> list[str]:
    """已向量化切片的 id(字符串形式,与 Milvus 主键口径一致),按 id 升序。"""
    with _session_scope(session) as active:
        ids = active.scalars(
            select(KnowledgeChunk.id)
            .where(KnowledgeChunk.vectorize_status == "done")
            .order_by(KnowledgeChunk.id)
        ).all()
        return [str(chunk_id) for chunk_id in ids]


def overview_counts(session: Session | None = None) -> dict[str, int]:
    """知识块总览:总数、待向量化、已完成、关键条款。"""
    with _session_scope(session) as active:
        chunks = active.scalar(
            select(func.count()).select_from(KnowledgeChunk)
        )
        pending = active.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.vectorize_status == "pending")
        )
        done = active.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.vectorize_status == "done")
        )
        key_clauses = active.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.is_key_clause.is_(True))
        )
        return {
            "chunks": int(chunks or 0),
            "pending": int(pending or 0),
            "done": int(done or 0),
            "key_clauses": int(key_clauses or 0),
        }


def list_recent(
    limit: int = 20, session: Session | None = None
) -> list[StoredChunk]:
    """按 id 倒序取最近写入的切片。"""
    with _session_scope(session) as active:
        rows = active.scalars(
            select(KnowledgeChunk).order_by(KnowledgeChunk.id.desc()).limit(limit)
        ).all()
        return [_to_stored(row) for row in rows]


def reset_all_pending(session: Session | None = None) -> None:
    """把所有切片重置为待向量化(清空 vector_id),供 rebuild 重刷。"""
    with _session_scope(session) as active:
        active.execute(
            update(KnowledgeChunk).values(
                vectorize_status="pending", vector_id=None
            )
        )


def delete_all_chunks(session: Session | None = None) -> None:
    """先清空自引用前后指针,再删除全部切片(避免自引用外键阻塞)。"""
    with _session_scope(session) as active:
        active.execute(
            update(KnowledgeChunk).values(
                prev_chunk_id=None, next_chunk_id=None
            )
        )
        active.execute(delete(KnowledgeChunk))


# ---------------------------------------------------------------------------
# qa_extraction_staging
# ---------------------------------------------------------------------------


def staging_counts(session: Session | None = None) -> dict[str, int]:
    """暂存表状态计数,以及不同批次号数量。"""
    with _session_scope(session) as active:
        rows = active.execute(
            select(QaExtractionStaging.status, func.count())
            .select_from(QaExtractionStaging)
            .group_by(QaExtractionStaging.status)
        ).all()
        counts = {"extracted": 0, "kept": 0, "discarded": 0}
        for status, count in rows:
            counts[status] = int(count)
        batches = active.scalar(
            select(func.count(func.distinct(QaExtractionStaging.batch_no)))
        )
        counts["batches"] = int(batches or 0)
        return counts


def list_staging(
    status: str | None = None,
    limit: int = 50,
    session: Session | None = None,
) -> list[StagingRow]:
    """按 id 倒序列出暂存行;status 非空时只返回该状态。"""
    statement = select(QaExtractionStaging)
    if status is not None:
        statement = statement.where(QaExtractionStaging.status == status)
    statement = statement.order_by(QaExtractionStaging.id.desc()).limit(limit)
    with _session_scope(session) as active:
        rows = active.scalars(statement).all()
        return [
            StagingRow(
                id=row.id,
                batch_no=row.batch_no,
                source_ref=row.source_ref,
                question=row.question,
                answer=row.answer,
                status=row.status,
                created_at=row.created_at,
            )
            for row in rows
        ]


def insert_staging(
    rows: Iterable[StagingCandidate], session: Session | None = None
) -> list[int]:
    """按输入顺序插入暂存行并返回自增 id;status 用数据库默认 extracted。"""
    entities = [
        QaExtractionStaging(
            batch_no=row.batch_no,
            source_ref=row.source_ref,
            question=row.question,
            answer=row.answer,
        )
        for row in rows
    ]
    if not entities:
        return []
    with _session_scope(session) as active:
        active.add_all(entities)
        active.flush()
        return [entity.id for entity in entities]


def mark_staging(
    ids: Sequence[int], status: str, session: Session | None = None
) -> None:
    """批量更新暂存行状态(extracted / kept / discarded)。"""
    ids = list(ids)
    if not ids:
        return
    with _session_scope(session) as active:
        active.execute(
            update(QaExtractionStaging)
            .where(QaExtractionStaging.id.in_(ids))
            .values(status=status)
        )


def delete_staging(ids: Sequence[int], session: Session | None = None) -> None:
    """按 id 删除暂存行;用于批次处理失败时整批回滚。"""
    ids = list(ids)
    if not ids:
        return
    with _session_scope(session) as active:
        active.execute(
            delete(QaExtractionStaging).where(
                QaExtractionStaging.id.in_(ids)
            )
        )


# ---------------------------------------------------------------------------
# 对话挖掘素材
# ---------------------------------------------------------------------------


def list_conversations_for_mining(
    session: Session | None = None,
) -> list[ConversationForMining]:
    """列出至少含一条 user + 一条 assistant 非空消息的会话。

    - 忽略 tool 消息与空/纯空白 content;
    - turns 按 message_id 升序;
    - max_message_id 取会话内所有消息(含 tool)的最大 id。
    """
    with _session_scope(session) as active:
        conversations = active.execute(
            select(Conversation.id, Conversation.user_id).order_by(Conversation.id)
        ).all()

        max_ids = dict(
            active.execute(
                select(
                    Message.conversation_id, func.max(Message.id)
                ).group_by(Message.conversation_id)
            ).all()
        )

        message_rows = active.execute(
            select(Message.conversation_id, Message.id, Message.role, Message.content)
            .where(Message.role.in_(("user", "assistant")))
            .order_by(Message.id)
        ).all()

    buckets: dict[int, list[tuple[int, str, str]]] = {}
    for conversation_id, message_id, role, content in message_rows:
        if content is None or not content.strip():
            continue
        buckets.setdefault(conversation_id, []).append(
            (int(message_id), role, content)
        )

    result: list[ConversationForMining] = []
    for conversation_id, user_id in conversations:
        turns = buckets.get(conversation_id)
        if not turns:
            continue
        roles = {turn[1] for turn in turns}
        if "user" not in roles or "assistant" not in roles:
            continue
        max_message_id = max_ids.get(conversation_id)
        if max_message_id is None:
            continue
        result.append(
            ConversationForMining(
                id=conversation_id,
                user_id=user_id,
                max_message_id=int(max_message_id),
                turns=tuple(turns),
            )
        )
    return result


def staging_tail(
    conversation_id: int, session: Session | None = None
) -> int | None:
    """返回该会话已挖掘到的最大消息 id;无记录返回 None。

    约定 ``source_ref = conv:{conversation_id}:msgs:{first}-{last}``,只认
    完全匹配该格式的引用,取 ``last`` 的最大值。
    """
    pattern = re.compile(
        rf"^conv:{int(conversation_id)}:msgs:\d+-(\d+)$"
    )
    with _session_scope(session) as active:
        refs = active.scalars(
            select(QaExtractionStaging.source_ref).where(
                QaExtractionStaging.source_ref.like(
                    f"conv:{int(conversation_id)}:msgs:%"
                )
            )
        ).all()
    last_ids = [int(match.group(1)) for ref in refs if (match := pattern.match(ref or ""))]
    return max(last_ids) if last_ids else None
