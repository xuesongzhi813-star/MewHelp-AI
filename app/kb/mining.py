"""对话挖掘 job:把客服历史会话抽取为可入库的问答知识。

流程(``run_once``):
1. 先补跑一轮 ``dualwrite.vectorize_pending()``,避免上一轮 kept 的切片
   堆积在 pending;
2. 列出可挖掘会话,跳过 ``staging_tail >= max_message_id`` 的已挖会话;
3. 按 ``mining_batch_size`` 与 ``mining_max_chars`` 装箱,每批调用一次
   结构化输出模型;抽取成功后才写入 ``qa_extraction_staging``;
4. 逐条做精确判重与 Milvus 向量判重,命中标记 ``discarded``;
5. 保留项写入 ``knowledge_chunks``( ``content_type="对话挖掘"`` ),
   标记 ``kept`` 并立即向量化。

失败语义:
- LLM 调用失败直接冒泡,且该批不写任何暂存行;
- 判重 / 入库阶段的异常会先删除该批已写入的暂存行,再冒泡。这样
  ``source_ref`` 不会推进 ``staging_tail``,下一轮会重新抽取同一会话;
  失败前已经写入 ``knowledge_chunks`` 的问答由下一轮的精确判重兜底,
  不会产生重复切片。
"""

from __future__ import annotations

import argparse
import logging
import re
import secrets
import string
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from app.config import get_settings
from app.kb import dualwrite, milvus, store
from app.kb.documents import classify_category, normalize_text
from app.kb.embedding import embed_texts
from app.kb.store import ConversationForMining
from app.llm import build_chat_model

logger = logging.getLogger(__name__)

# 单条消息进入 Prompt 的最大字数;超出部分截断,避免一条超长消息挤掉同批
# 其他会话。
MAX_TURN_CHARS = 500

# 一次最多恢复多少条上次中断留下的 extracted 暂存行;超出的部分下一轮继续。
RESUME_LIMIT = 200

# 零抽取会话的审计哨兵:写入一行 discarded,让 staging_tail 前进,
# 避免 --interval 反复对同一会话调用 LLM。
NO_KNOWLEDGE_QUESTION = "（无可提取知识）"
NO_KNOWLEDGE_ANSWER = "该会话未抽取到可复用问答对。"

_SOURCE_REF_PATTERN = re.compile(r"^conv:(\d+):msgs:\d+-(\d+)$")


class QAPair(BaseModel):
    """从一段会话中抽取的一组问答。"""

    conversation_id: int = Field(description="该问答来自的会话 id")
    question: str = Field(description="用户问题的通用化表述")
    answer: str = Field(description="客服已经明确给出的回答")


class QAPairList(BaseModel):
    """一批会话的抽取结果;没有可沉淀内容时 pairs 为空。"""

    pairs: list[QAPair] = Field(default_factory=list)


@dataclass(frozen=True)
class MiningResult:
    """一轮挖掘的结果。"""

    conversations: int
    extracted: int
    kept: int
    discarded: int


PROMPT_HEADER = (
    "你是电商客服知识库的问答抽取器。请从下面的客服会话中抽取可沉淀为"
    "通用知识的问答对。要求:\n"
    "1. 每个问答对必须带 conversation_id,取值必须是下方「### 会话 {id}」中的编号;\n"
    "2. 问题用用户的口语原意概括,答案只能来自客服已经明确给出的回答,不得编造;\n"
    "3. 只提取可复用于其他用户的政策、流程、商品/尺码等知识;"
    "不要提取订单号、物流单号、地址等一次性信息;\n"
    "4. 纯寒暄,或用户提问但客服没有给出有效答案的,不要提取;\n"
    "5. 没有可提取内容时返回空列表 pairs=[]。"
)


def build_mining_model(settings) -> Runnable:
    """在 chat model 上挂结构化输出,返回可直接 ``.pairs`` 的 Runnable。"""
    return build_chat_model(settings).with_structured_output(
        QAPairList, method="function_calling"
    )


# ---------------------------------------------------------------------------
# Prompt 与批次装箱
# ---------------------------------------------------------------------------


def _conversation_block(conversation: ConversationForMining) -> str:
    """把一个会话渲染成 ``### 会话 {id}`` + 用户/客服轮次。"""
    lines = [f"### 会话 {conversation.id}"]
    for _, role, content in conversation.turns:
        label = "用户" if role == "user" else "客服"
        text = (content or "").strip()
        if len(text) > MAX_TURN_CHARS:
            text = text[:MAX_TURN_CHARS] + "…"
        lines.append(f"{label}:{text}")
    return "\n".join(lines)


def build_mining_prompt(conversations: Sequence[ConversationForMining]) -> str:
    """拼接挖掘 Prompt,单条消息截断 500 字、总量截断到 ``mining_max_chars``。"""
    settings = get_settings()
    body = "\n\n".join(_conversation_block(item) for item in conversations)
    prompt = PROMPT_HEADER + ("\n\n" + body if body else "")
    return prompt[: settings.mining_max_chars]


def _plan_batches(
    conversations: Sequence[ConversationForMining], settings
) -> list[list[ConversationForMining]]:
    """按会话数与 Prompt 字数上限装箱,保证每批不会超过 ``mining_max_chars``。

    单条会话自身超限时仍然单独成批,由 :func:`build_mining_prompt` 截断。
    """
    batch_size = max(1, settings.mining_batch_size)
    max_chars = max(1, settings.mining_max_chars)
    budget = max(1, max_chars - len(PROMPT_HEADER) - 2)

    batches: list[list[ConversationForMining]] = []
    current: list[ConversationForMining] = []
    current_length = 0
    for conversation in conversations:
        block_length = len(_conversation_block(conversation))
        separator = 2 if current else 0
        if current and (
            len(current) >= batch_size
            or current_length + separator + block_length > budget
        ):
            batches.append(current)
            current = []
            current_length = 0
            separator = 0
        current.append(conversation)
        current_length += separator + block_length
    if current:
        batches.append(current)
    return batches


def _new_batch_no() -> str:
    """生成 ``M{YYYYMMDDHHMMSS}-{8位随机字符}`` 批次号。"""
    stamp = datetime.now().strftime("M%Y%m%d%H%M%S")
    suffix = "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8)
    )
    return f"{stamp}-{suffix}"


def _source_ref(conversation: ConversationForMining) -> str:
    first_message_id = conversation.turns[0][0]
    return (
        f"conv:{conversation.id}:msgs:"
        f"{first_message_id}-{conversation.max_message_id}"
    )


# ---------------------------------------------------------------------------
# 一轮挖掘
# ---------------------------------------------------------------------------


def _pending_conversations(limit: int | None) -> list[ConversationForMining]:
    """列出尚未挖到最新消息的会话,最多 ``limit`` 个。"""
    ready: list[ConversationForMining] = []
    for conversation in store.list_conversations_for_mining():
        tail = store.staging_tail(conversation.id)
        if tail is not None and tail >= conversation.max_message_id:
            continue
        ready.append(conversation)
    if limit is not None:
        ready = ready[: max(0, limit)]
    return ready


def _require_vectorize_complete(
    result: dualwrite.VectorizeResult, *, stage: str
) -> None:
    """向量化有失败或剩余 pending 时显式失败,避免作业假装成功。"""
    if result.failed > 0 or result.pending_left > 0:
        raise RuntimeError(
            f"{stage}:向量化未完成"
            f"(failed={result.failed}, pending_left={result.pending_left}),"
            "补跑 vectorize 后再试"
        )


def _conversation_id_from_source_ref(source_ref: str | None) -> int | None:
    """从 ``conv:{id}:msgs:{first}-{last}`` 还原会话 id;格式不符返回 None。"""
    match = _SOURCE_REF_PATTERN.match(source_ref or "")
    return int(match.group(1)) if match else None


def _dedup_and_keep(
    batch_rows: Sequence[tuple[int, QAPair, ConversationForMining]],
    *,
    existing_questions: set[str],
    kept_questions: set[str],
    settings,
) -> tuple[int, int]:
    """对一批暂存行判重并保留;返回 ``(kept, discarded)``。"""
    kept = 0
    discarded = 0
    for staging_id, pair, conversation in batch_rows:
        normalized = normalize_text(pair.question)
        if normalized in existing_questions or normalized in kept_questions:
            store.mark_staging([staging_id], "discarded")
            discarded += 1
            continue

        vector = embed_texts([pair.question])[0]
        matches = milvus.search(vector, 1)
        if matches and matches[0][1] >= settings.knowledge_dedup_score:
            store.mark_staging([staging_id], "discarded")
            discarded += 1
            continue

        store.insert_chunks(
            [
                store.ChunkCandidate(
                    category=classify_category(pair.question, pair.answer),
                    questions=pair.question,
                    answer=pair.answer,
                    section_path=f"对话挖掘 > 会话 {conversation.id}",
                    content_type="对话挖掘",
                    is_key_clause=False,
                )
            ]
        )
        store.mark_staging([staging_id], "kept")
        kept_questions.add(normalized)
        kept += 1
        _require_vectorize_complete(
            dualwrite.vectorize_pending(), stage="挖掘入库后向量化"
        )
    return kept, discarded


def _resume_extracted_rows(
    *,
    existing_questions: set[str],
    kept_questions: set[str],
    settings,
) -> tuple[int, int]:
    """恢复上次中断留下的 ``status='extracted'`` 暂存行。

    进程在写暂存 / 去重中途被杀时,这些行会一直占用 ``staging_tail``,
    导致对应会话被永久跳过。这里直接复用同一套判重/入库路径,不再调用
    LLM、也不新增暂存行。失败时行保持 ``extracted``,下一轮继续。
    """
    rows = store.list_staging(status="extracted", limit=RESUME_LIMIT)
    if not rows:
        return 0, 0

    conversations = {
        item.id: item for item in store.list_conversations_for_mining()
    }
    pending: list[tuple[int, QAPair, ConversationForMining]] = []
    discarded = 0
    for row in rows:
        if row.question == NO_KNOWLEDGE_QUESTION:
            # 哨兵行在插入与标记 discarded 之间崩溃时的兜底。
            store.mark_staging([row.id], "discarded")
            discarded += 1
            continue

        conversation_id = _conversation_id_from_source_ref(row.source_ref)
        conversation = (
            conversations.get(conversation_id)
            if conversation_id is not None
            else None
        )
        if conversation is None:
            logger.warning(
                "恢复暂存行 %s 时找不到对应会话(source_ref=%s),标记 discarded",
                row.id,
                row.source_ref,
            )
            store.mark_staging([row.id], "discarded")
            discarded += 1
            continue

        pending.append(
            (
                row.id,
                QAPair(
                    conversation_id=conversation.id,
                    question=row.question,
                    answer=row.answer,
                ),
                conversation,
            )
        )

    if not pending:
        return 0, discarded
    kept, discarded_here = _dedup_and_keep(
        pending,
        existing_questions=existing_questions,
        kept_questions=kept_questions,
        settings=settings,
    )
    return kept, discarded + discarded_here


def run_once(limit: int | None = None) -> MiningResult:
    """执行一轮对话挖掘;``limit`` 限制本轮新处理的会话数。

    顺序:补向量化 → 恢复上次中断的 extracted 行 → 处理新会话。
    恢复行的 kept/discarded 计入返回值,但不计入 ``conversations`` /
    ``extracted``(它们不是本轮 LLM 新抽取的)。
    """
    settings = get_settings()
    # 第一步永远是补向量化:上一轮 kept 的切片可能仍在 pending。
    _require_vectorize_complete(
        dualwrite.vectorize_pending(), stage="挖掘前补向量化"
    )

    existing_questions = store.existing_questions_normalized()
    kept_questions: set[str] = set()
    resumed_kept, resumed_discarded = _resume_extracted_rows(
        existing_questions=existing_questions,
        kept_questions=kept_questions,
        settings=settings,
    )

    conversations = _pending_conversations(limit)
    if not conversations:
        return MiningResult(
            conversations=0,
            extracted=0,
            kept=resumed_kept,
            discarded=resumed_discarded,
        )

    extractor = build_mining_model(settings)
    by_id = {conversation.id: conversation for conversation in conversations}
    extracted = 0
    kept = resumed_kept
    discarded = resumed_discarded

    for batch in _plan_batches(conversations, settings):
        batch_no = _new_batch_no()
        result = extractor.invoke(build_mining_prompt(batch))
        pairs = getattr(result, "pairs", None) or []

        rows: list[store.StagingCandidate] = []
        pending_rows: list[tuple[QAPair, ConversationForMining]] = []
        produced_conversation_ids: set[int] = set()
        for pair in pairs:
            conversation = by_id.get(pair.conversation_id)
            if conversation is None:
                logger.warning(
                    "跳过未知 conversation_id=%s 的问答对: %s",
                    pair.conversation_id,
                    pair.question,
                )
                continue
            question = (pair.question or "").strip()
            answer = (pair.answer or "").strip()
            if not question or not answer:
                logger.warning(
                    "跳过空问题或空答案(conversation_id=%s)",
                    pair.conversation_id,
                )
                continue
            rows.append(
                store.StagingCandidate(
                    batch_no=batch_no,
                    source_ref=_source_ref(conversation),
                    question=question,
                    answer=answer,
                )
            )
            pending_rows.append(
                (
                    QAPair(
                        conversation_id=pair.conversation_id,
                        question=question,
                        answer=answer,
                    ),
                    conversation,
                )
            )
            produced_conversation_ids.add(conversation.id)

        # 抽取调用成功后才落暂存。
        staging_ids = store.insert_staging(rows)
        extracted += len(staging_ids)
        sentinel_ids: list[int] = []

        try:
            kept_here, discarded_here = _dedup_and_keep(
                [
                    (staging_id, pair, conversation)
                    for staging_id, (pair, conversation) in zip(
                        staging_ids, pending_rows
                    )
                ],
                existing_questions=existing_questions,
                kept_questions=kept_questions,
                settings=settings,
            )

            # 零抽取会话也要留一条 discarded 哨兵:否则 staging_tail 永远
            # 不前进,--interval 会反复对同一会话调用 LLM。
            sentinel_ids = store.insert_staging(
                [
                    store.StagingCandidate(
                        batch_no=batch_no,
                        source_ref=_source_ref(conversation),
                        question=NO_KNOWLEDGE_QUESTION,
                        answer=NO_KNOWLEDGE_ANSWER,
                    )
                    for conversation in batch
                    if conversation.id not in produced_conversation_ids
                ]
            )
            if sentinel_ids:
                store.mark_staging(sentinel_ids, "discarded")
        except Exception as exc:  # noqa: BLE001 - 整批回滚后原样冒泡
            # 暂存行一旦保留,source_ref 会推进 staging_tail,导致会话永远
            # 被跳过;因此批次处理失败时必须先删掉本批暂存行再冒泡。
            cleanup_ids = [*staging_ids, *sentinel_ids]
            logger.warning(
                "批次 %s 判重/入库失败,删除本批 %d 条暂存行后重试: %s",
                batch_no,
                len(cleanup_ids),
                exc,
            )
            try:
                store.delete_staging(cleanup_ids)
            except Exception:  # noqa: BLE001 - 不能掩盖原始异常
                logger.exception("删除批次 %s 的暂存行失败", batch_no)
            raise
        kept += kept_here
        discarded += discarded_here

    return MiningResult(
        conversations=len(conversations),
        extracted=extracted,
        kept=kept,
        discarded=discarded,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_result(result: MiningResult) -> None:
    print(
        f"对话挖掘完成:处理 {result.conversations} 个会话,"
        f"抽取 {result.extracted} 条问答,保留 {result.kept} 条,"
        f"丢弃 {result.discarded} 条。"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.kb.mining",
        description="MewHelp 对话挖掘 job",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只运行一轮(默认行为)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="本轮最多处理的会话数",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="循环运行的间隔秒数;不传则只跑一轮",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口:成功返回 0,异常打印中文错误到 stderr 并返回 1。"""
    args = _build_parser().parse_args(argv)
    try:
        while True:
            _print_result(run_once(limit=args.limit))
            if args.once or args.interval is None or args.interval <= 0:
                break
            time.sleep(args.interval)
    except Exception as exc:  # noqa: BLE001 - CLI 统一转成退出码 1
        print(f"错误:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
