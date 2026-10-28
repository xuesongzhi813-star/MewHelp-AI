"""在线检索:query → 向量 → Milvus 召回 → MySQL 回表 → Hit / 展示文本。

设计约定:
- 空白 query 直接短路返回,不消耗 embedding / Milvus 调用;
- 分数阈值过滤在回表前完成,只按需查询 MySQL;
- Milvus 命中但 MySQL 缺失的 id 跳过并记 warning(双写短暂不一致时仍可用);
- 不捕获 EmbeddingError / MilvusUnavailable,交由 execute_tool 统一转成工具错误。
"""

from __future__ import annotations

import logging
from dataclasses import replace, asdict
from app.observability.spans import observed_call

from app.config import get_settings
from app.kb import milvus, store
from app.kb.embedding import embed_texts
from app.kb.contracts import Hit, QueryPlan, RetrievalFilters, RetrievalResult, RetrievalStrategy
from app.kb.catalog import infer_product_category
from app.kb.documents import embedding_text
from app.kb.query import build_query_plan
from app.kb.reranker import rerank

logger = logging.getLogger(__name__)


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

    if getattr(settings, "kb_corpus_version", "ch03") == "ch04":
        # 挖掘去重的旧入口始终保持dense/COSINE，不把RRF分数当相似度。
        plan = QueryPlan(raw_query=query, normalized_query=query, lexical_query=query)
        result = retrieve_result(query, strategy="dense", plan=plan)
        return [hit for hit in result.hits if hit.score >= threshold][:limit]

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
    if getattr(get_settings(), "kb_corpus_version", "ch03") == "ch04":
        hits = retrieve_result(query).hits
    else:
        hits = search_hits(query)
    if not hits:
        return f"FAQ 库中没有找到与「{query}」相关的条目"
    return "\n\n".join(
        f"{index}. {hit.questions}\n→ {hit.answer}"
        for index, hit in enumerate(hits, 1)
    )


@observed_call('knowledge-retrieval', input_fields=('query', 'strategy', 'filters'),
               output_selector=lambda result: {'strategy':result.strategy,
                   'candidate_ids':list(result.candidate_ids), 'hits':[asdict(hit) for hit in result.hits],
                   'reason':result.reason})
def retrieve_result(query: str, *, strategy: RetrievalStrategy | None = None,
                    plan: QueryPlan | None = None,
                    filters: RetrievalFilters | None = None,
                    query_vector: list[float] | None = None) -> RetrievalResult:
    settings = get_settings()
    selected = strategy or getattr(settings, "knowledge_strategy", "dense")
    if selected not in ("dense", "bm25", "hybrid", "hybrid_rerank"):
        raise ValueError("未知检索策略")
    if not query or not query.strip():
        empty = plan or QueryPlan(raw_query=query, normalized_query="", lexical_query="")
        return RetrievalResult(empty, selected, reason="empty_query")
    if getattr(settings, "kb_corpus_version", "ch03") != "ch04":
        if selected != "dense":
            raise ValueError("混合策略需要已验证的ch04迁移")
        legacy_plan = plan or QueryPlan(raw_query=query, normalized_query=query, lexical_query=query,
                                       filters=filters or RetrievalFilters())
        if any(value for key, value in legacy_plan.filters.model_dump().items() if key != "include_general"):
            raise ValueError("旧集合不支持元数据过滤，请迁移ch04")
        hits = tuple(search_hits(legacy_plan.normalized_query, top_k=50, min_score=-1))
        return RetrievalResult(legacy_plan, selected, hits, tuple(h.chunk_id for h in hits))
    from app.kb.migration import load_manifest
    manifest = load_manifest()
    if manifest.phase != "validated":
        raise ValueError("ch04迁移尚未验证，禁止搜索未完成的新集合")
    plan = plan if plan is not None else build_query_plan(query, filters=filters)
    if not plan.normalized_query.strip():
        return RetrievalResult(plan, selected, reason="empty_query")
    search_kwargs = {"collection_name": settings.hybrid_collection, "filters": plan.filters}
    if selected == "bm25":
        candidates = milvus.search_bm25(plan.lexical_query, 50, **search_kwargs)
    else:
        vector = query_vector if query_vector is not None else embed_texts([plan.normalized_query])[0]
        if selected == "dense":
            candidates = milvus.search_dense(vector, 50, **search_kwargs)
        else:
            candidates = milvus.search_hybrid(vector, plan.lexical_query, **search_kwargs)
    # 服务端已融合去重；额外防御重复ID，保留首次出现的相关性顺序。
    unique = {}
    excluded = set(manifest.legacy_excluded_ids)
    for key, score in candidates:
        key = int(key)
        if key not in excluded:
            unique.setdefault(key, float(score))
    stored_by_id = store.fetch_by_ids(list(unique))
    score_type = "fusion" if selected in ("hybrid", "hybrid_rerank") else selected
    hits = []
    missing = 0
    for key, score in unique.items():
        row = stored_by_id.get(key)
        if row is None:
            missing += 1
            continue
        scores = {"dense_score": None, "bm25_score": None, "fusion_score": None}
        scores[score_type + "_score"] = score
        hits.append(Hit(key, score, row.category, row.questions, row.answer,
                        row.content_type or "", row.section_path or "",
                        product_category=infer_product_category(row.section_path or "", row.questions, row.answer),
                        score_type=score_type, **scores))
    if missing:
        logger.warning("召回候选中MySQL缺失%d条，已跳过", missing)
    candidate_ids = tuple(h.chunk_id for h in hits)
    if selected == "hybrid_rerank" and hits:
        ranking = rerank(plan.normalized_query,
                         [embedding_text(h.category, h.questions, h.answer) for h in hits], top_n=10)
        hits = [replace(hits[index], score=score, score_type="rerank", rerank_score=score)
                for index, score in ranking]
    return RetrievalResult(plan, selected, tuple(hits), candidate_ids,
                           reason=f"missing_mysql:{missing}" if missing else ("no_hits" if not hits else ""))
