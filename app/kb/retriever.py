"""在线检索:query → 向量 → Milvus 召回 → MySQL 回表 → Hit / 展示文本。

设计约定:
- 空白 query 直接短路返回,不消耗 embedding / Milvus 调用;
- 分数阈值过滤在回表前完成,只按需查询 MySQL;
- Milvus 命中但 MySQL 缺失的 id 跳过并记 warning(双写短暂不一致时仍可用);
- 不捕获 EmbeddingError / MilvusUnavailable,交由 execute_tool 统一转成工具错误。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import get_settings
from app.kb import milvus, store
from app.kb.embedding import embed_texts

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hit:
    """一条检索命中:向量分数 + MySQL 中的完整切片内容。"""

    chunk_id: int
    score: float
    category: str
    questions: str
    answer: str
    content_type: str
    section_path: str


def search_hits(
    query: str,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[Hit]:
    """语义检索知识块,按 Milvus 相似度顺序返回通过阈值的命中。

    Args:
        query: 用户原话或问题;空白时直接返回空列表。
        top_k: 召回条数,默认 ``settings.faq_max_results``。
        min_score: 余弦分数下限(含边界),默认 ``settings.knowledge_min_score``。

    Raises:
        EmbeddingError: 向量化失败。
        MilvusUnavailable: Milvus 不可用。
    """
    if not query or not query.strip():
        return []

    settings = get_settings()
    limit = settings.faq_max_results if top_k is None else top_k
    threshold = settings.knowledge_min_score if min_score is None else min_score

    vector = embed_texts([query])[0]
    candidates = [
        (str(chunk_id), float(score))
        for chunk_id, score in milvus.search(vector, limit)
        if float(score) >= threshold
    ]
    if not candidates:
        return []

    stored_by_id = store.fetch_by_ids([int(chunk_id) for chunk_id, _ in candidates])

    hits: list[Hit] = []
    for chunk_id, score in candidates:
        stored = stored_by_id.get(int(chunk_id))
        if stored is None:
            # Milvus 已写入但 MySQL 尚无对应行(双写间隙),跳过不影响其余命中。
            logger.warning(
                "Milvus 命中 id=%s(score=%.4f),但 MySQL 中不存在,已跳过",
                chunk_id,
                score,
            )
            continue
        hits.append(
            Hit(
                chunk_id=int(chunk_id),
                score=score,
                category=stored.category,
                questions=stored.questions,
                answer=stored.answer,
                content_type=stored.content_type or "",
                section_path=stored.section_path or "",
            )
        )
    return hits


def retrieve(query: str) -> str:
    """把检索命中格式化为「序号. 问题 + → 答案」文本,供 query_faq 工具返回。"""
    hits = search_hits(query)
    if not hits:
        return f"FAQ 库中没有找到与「{query}」相关的条目"
    return "\n\n".join(
        f"{index}. {hit.questions}\n→ {hit.answer}"
        for index, hit in enumerate(hits, 1)
    )
