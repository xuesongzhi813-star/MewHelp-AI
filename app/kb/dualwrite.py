"""MySQL 与 Milvus 双写状态机。

职责:
- 把 ``knowledge_chunks`` 中 pending 的行批量嵌入并 upsert 到 Milvus;
- 单批失败只累计 ``failed`` 并继续后续批次,失败行保持 pending,支持断点续跑;
- 重建 / 清空知识库,以及 MySQL 与 Milvus 的一致性核对。

失败语义:
- ``vectorize_pending`` 把嵌入 / Milvus 异常折算为 ``failed`` 计数,不中断整轮;
- ``consistency_snapshot`` 读不到任一侧时返回 ``unknown``,
  绝不把「读不到」当成「两边没有匹配」。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import get_settings
from app.kb import milvus, store
from app.kb.documents import embedding_text
from app.kb.embedding import embed_texts

logger = logging.getLogger(__name__)

# milvus.list_ids() 默认最多返回 5000 条;超过该规模时无法做精确集合比对,
# 只能降级为数量比对,避免把「没列全」误报成 missing。
_EXACT_ID_LIMIT = 5000
_COUNT_ONLY_NOTE = "仅数量比对(超过 5000 行)"


@dataclass(frozen=True)
class VectorizeResult:
    """一轮向量化的结果。"""

    processed: int
    failed: int
    pending_left: int


@dataclass(frozen=True)
class Consistency:
    """一致性快照;missing/orphan 均以字符串主键表示并已排序。"""

    status: str
    missing_in_milvus: tuple[str, ...]
    orphan_in_milvus: tuple[str, ...]
    note: str


def vectorize_pending(limit: int | None = None) -> VectorizeResult:
    """批量向量化 pending 切片,失败批次保持 pending 供下次续跑。

    Args:
        limit: 本次最多尝试的切片数;``None`` 表示直到没有 pending 为止。

    Returns:
        ``processed`` 为成功写入的行数,``failed`` 为失败批次的行数,
        ``pending_left`` 为本轮结束后的全局待处理行数。
    """
    settings = get_settings()
    batch_size = max(1, settings.embedding_batch_size)
    processed = 0
    failed = 0
    # iter_pending 只支持 limit、没有游标:记录本轮已尝试过的 id,
    # 下一轮多取 len(seen) 条再过滤,避免失败行被反复取到造成死循环。
    seen: set[int] = set()

    while limit is None or processed + failed < limit:
        batch = [
            item
            for item in store.iter_pending(batch_size + len(seen))
            if item.id not in seen
        ]
        if not batch:
            break
        if limit is not None:
            remaining = limit - (processed + failed)
            if remaining <= 0:
                break
            batch = batch[:remaining]

        ids = [item.id for item in batch]
        seen.update(ids)
        texts = [
            embedding_text(item.category, item.questions, item.answer)
            for item in batch
        ]
        try:
            vectors = embed_texts(texts)
            if len(vectors) != len(batch):
                raise ValueError(
                    f"嵌入返回数量不匹配:期望 {len(batch)},实际 {len(vectors)}"
                )
            milvus.upsert([str(item_id) for item_id in ids], vectors)
            # 先 flush 再标 done:任何一步失败都让该批保持 pending,
            # 下次重跑用幂等 upsert 覆盖,不会出现 done 但未持久化的情况。
            milvus.flush()
            store.mark_done(ids)
        except Exception as exc:  # noqa: BLE001 - 单批失败不阻断其余批次
            failed += len(batch)
            logger.exception("批次向量化失败(ids=%s): %s", ids, exc)
            continue
        processed += len(batch)

    pending_left = store.overview_counts()["pending"]
    return VectorizeResult(
        processed=processed, failed=failed, pending_left=pending_left
    )


def rebuild_all() -> VectorizeResult:
    """清空 Milvus 后按 MySQL 全量重建向量索引。"""
    store.reset_all_pending()
    milvus.drop()
    milvus.ensure_collection()
    return vectorize_pending()


def reset_all() -> None:
    """删除全部知识切片并清空 Milvus 集合。"""
    store.delete_all_chunks()
    milvus.drop()


def consistency_snapshot() -> Consistency:
    """按精确 id 比对 MySQL(done)与 Milvus 的主键集合。"""
    try:
        reachable = milvus.ping()
    except Exception as exc:  # noqa: BLE001 - 可用性探测失败一律 unknown
        logger.warning("Milvus 可用性探测失败: %s", exc)
        reachable = False
    if not reachable:
        return Consistency("unknown", (), (), "Milvus 读不到")

    try:
        mysql_ids = set(store.done_ids())
    except Exception as exc:  # noqa: BLE001 - 读取失败不能当成无匹配
        logger.warning("读取 MySQL done id 失败: %s", exc)
        return Consistency("unknown", (), (), "MySQL 读不到")

    try:
        milvus_count = milvus.count()
    except Exception as exc:  # noqa: BLE001 - 读取失败不能当成无匹配
        logger.warning("读取 Milvus 数量失败: %s", exc)
        return Consistency("unknown", (), (), "Milvus 读不到")

    if len(mysql_ids) > _EXACT_ID_LIMIT or milvus_count > _EXACT_ID_LIMIT:
        # list_ids 有条数上限,大库下精确集合比对会假报 missing;降级为数量比对。
        status = "consistent" if milvus_count == len(mysql_ids) else "mismatch"
        return Consistency(status, (), (), _COUNT_ONLY_NOTE)

    try:
        milvus_ids = set(milvus.list_ids())
    except Exception as exc:  # noqa: BLE001 - 读取失败不能当成无匹配
        logger.warning("读取 Milvus id 失败: %s", exc)
        return Consistency("unknown", (), (), "Milvus 读不到")

    missing = tuple(sorted(mysql_ids - milvus_ids))
    orphan = tuple(sorted(milvus_ids - mysql_ids))
    note = "已完成精确 id 比对"
    if not missing and not orphan:
        return Consistency("consistent", (), (), note)
    return Consistency("mismatch", missing, orphan, note)
