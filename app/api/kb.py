"""``/api/kb`` 知识库路由:总览、材料、预览、录入、向量化、暂存与检索自测。

设计约定:
- ``overview`` 按依赖逐块降级:MySQL / Milvus 读不到是一种状态,不是异常;
  材料清单现场切分,不依赖数据库;
- ``preview`` 是纯 dry-run,不触达 MySQL / Milvus / 嵌入 API;
- ``ingest`` 先落 MySQL(pending),再按需向量化;向量化失败返回 502 但
  绝不回滚已写入的行,重跑 ``vectorize`` 即可补齐;
- ``source`` 只按 ``sources.SOURCES`` 白名单读文件,绝不读取客户端任意路径;
- 所有失败统一 ``{"detail": "..."}``;未知检索路线由 Pydantic ``Literal``
  直接返回 422。
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import get_settings
from app.core.jobs import list_jobs
from app.db import get_session_local
from app.kb import milvus, sources, store
from app.kb.documents import Source, build_chunks, chunk_fingerprint
from app.kb.dualwrite import consistency_snapshot, vectorize_pending
from app.kb.retriever import search_hits
from app.kb.sources import get_sources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["kb"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------


class PreviewRequest(BaseModel):
    markdown: str
    source_title: str
    content_type: str = "文档章节"


class IngestRequest(PreviewRequest):
    vectorize: bool = True


class VectorizeRequest(BaseModel):
    limit: int | None = None


class SearchRequest(BaseModel):
    query: str
    top_k: int | None = None
    # 本章只有稠密检索;未知路线由 Pydantic 返回 422。
    route: Literal["dense"] = "dense"


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _check_markdown_size(markdown: str) -> None:
    """超过配置的字节上限时返回 413,不截断、不写入。"""
    limit = get_settings().kb_max_markdown_bytes
    size = len(markdown.encode("utf-8"))
    if size > limit:
        raise HTTPException(
            status_code=413,
            detail=f"Markdown 体积 {size} 字节,超过 {limit} 字节上限,未处理",
        )


def _inline_source(payload: PreviewRequest) -> Source:
    """手工录入没有文件路径,用固定虚拟路径承载来源元数据。"""
    return Source(Path("inline.md"), payload.source_title, payload.content_type)


def _existing_fingerprints() -> set[str]:
    """读取全表指纹;独立函数便于 API 测试替换。"""
    return store.existing_fingerprints()


def _persist_chunks(candidates: list[store.ChunkCandidate]) -> list[int]:
    """在单个事务内插入切片并串联前后指针,返回自增 id。"""
    if not candidates:
        return []
    with get_session_local()() as session:
        with session.begin():
            ids = store.insert_chunks(candidates, session=session)
            store.link_siblings(ids, session=session)
            return ids


def _consistency_payload() -> dict:
    snapshot = consistency_snapshot()
    return {
        "status": snapshot.status,
        "missing_in_milvus": list(snapshot.missing_in_milvus),
        "orphan_in_milvus": list(snapshot.orphan_in_milvus),
        "note": snapshot.note,
    }


def _unknown_consistency(note: str = "读不到") -> dict:
    return {
        "status": "unknown",
        "missing_in_milvus": [],
        "orphan_in_milvus": [],
        "note": note,
    }


def _source_overview(source: Source) -> dict:
    """现场切分一份材料;读取失败只影响该项。"""
    item: dict = {
        "path": source.path.as_posix(),
        "title": source.title,
        "content_type": source.content_type,
    }
    try:
        markdown = source.path.read_text(encoding="utf-8")
        chunks = build_chunks(markdown, source)
    except Exception as exc:  # noqa: BLE001 - 单份材料失败不连坐其余材料
        logger.warning("材料 %s 读取/切分失败: %s", source.path, exc)
        item.update(
            {
                "chunks": 0,
                "key_clauses": 0,
                "features": [],
                "error": "读不到",
            }
        )
        return item
    item.update(
        {
            "chunks": len(chunks),
            "key_clauses": sum(1 for chunk in chunks if chunk.is_key_clause),
            "features": sorted(
                {feature for chunk in chunks for feature in chunk.features}
            ),
        }
    )
    return item


# ---------------------------------------------------------------------------
# 总览
# ---------------------------------------------------------------------------


@router.get("/overview")
def get_overview() -> dict:
    """七块总览;任一外部依赖失败只降级对应块。"""
    settings = get_settings()

    mysql: dict
    staging: dict | None = None
    recent: list[dict] = []
    try:
        counts = store.overview_counts()
        staging = store.staging_counts()
        recent = [asdict(row) for row in store.list_recent(20)]
        mysql = {"ok": True, **counts}
    except Exception as exc:  # noqa: BLE001 - MySQL 不可达是一种状态
        logger.warning("读取 MySQL 总览失败: %s", exc)
        mysql = {
            "ok": False,
            "chunks": None,
            "pending": None,
            "done": None,
            "key_clauses": None,
        }
        staging = None
        recent = []

    milvus_ok = False
    milvus_count: int | None = None
    milvus_note: str | None = None
    try:
        milvus_ok = bool(milvus.ping())
        if milvus_ok:
            if not milvus.has_collection():
                # 服务可达但集合尚未创建(全新安装 / kb-reset 之后):
                # 这是正常的空状态,不是「读不到」。
                milvus_count = 0
                milvus_note = "集合未创建"
            else:
                milvus_count = int(milvus.count())
    except Exception as exc:  # noqa: BLE001 - Milvus 不可达是一种状态
        logger.warning("读取 Milvus 总览失败: %s", exc)
        milvus_ok = False
        milvus_count = None
        milvus_note = None

    try:
        consistency = _consistency_payload()
    except Exception as exc:  # noqa: BLE001 - 一致性探测失败一律 unknown
        logger.warning("一致性快照失败: %s", exc)
        consistency = _unknown_consistency()

    if not milvus_ok:
        # 读不到 Milvus 时绝不能报 consistent/mismatch。
        consistency = _unknown_consistency("Milvus 读不到")

    try:
        jobs = list_jobs()
        jobs_ok = True
    except Exception as exc:  # noqa: BLE001 - 作业运行器不可用时留空
        logger.warning("读取作业列表失败: %s", exc)
        jobs = []
        jobs_ok = False

    milvus_block: dict = {
        "ok": milvus_ok,
        "count": milvus_count,
        "collection": settings.knowledge_collection,
    }
    if milvus_note is not None:
        milvus_block["note"] = milvus_note

    return {
        "mysql": mysql,
        "milvus": milvus_block,
        "consistency": consistency,
        "staging": staging,
        "sources": [_source_overview(source) for source in get_sources()],
        "recent": recent,
        "jobs": jobs,
        "jobs_ok": jobs_ok,
    }


# ---------------------------------------------------------------------------
# 材料清单 / 预览
# ---------------------------------------------------------------------------


@router.get("/source")
def get_source(path: str) -> dict:
    """按白名单返回一份建库材料的完整 Markdown。"""
    normalized = Path(path).as_posix()
    source = next(
        (
            item
            for item in sources.SOURCES
            if item.path.as_posix() == normalized
        ),
        None,
    )
    if source is None:
        raise HTTPException(status_code=404, detail="材料不在白名单内")
    try:
        markdown = source.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=404, detail=f"材料读不到:{exc}") from exc
    return {
        "path": source.path.as_posix(),
        "title": source.title,
        "content_type": source.content_type,
        "markdown": markdown,
    }


@router.post("/preview")
def post_preview(payload: PreviewRequest) -> dict:
    """只做切分预览:不写 MySQL、不调嵌入、不碰 Milvus。"""
    _check_markdown_size(payload.markdown)
    chunks = build_chunks(payload.markdown, _inline_source(payload))
    return {
        "counts": {
            "chunks": len(chunks),
            "key_clauses": sum(1 for chunk in chunks if chunk.is_key_clause),
        },
        "features": sorted(
            {feature for chunk in chunks for feature in chunk.features}
        ),
        "chunks": [asdict(chunk) for chunk in chunks],
    }


# ---------------------------------------------------------------------------
# 录入 / 向量化
# ---------------------------------------------------------------------------


@router.post("/ingest")
def post_ingest(payload: IngestRequest):
    """按指纹跳过重复块后入库;可选顺手向量化。

    向量化失败返回 502,但已写入的行保持 pending,不回滚。
    """
    _check_markdown_size(payload.markdown)
    chunks = build_chunks(payload.markdown, _inline_source(payload))

    try:
        seen = set(_existing_fingerprints())
    except Exception as exc:  # noqa: BLE001 - MySQL 不可达统一 502
        raise HTTPException(
            status_code=502, detail=f"MySQL 不可达,读取去重指纹失败:{exc}"
        ) from exc

    candidates: list[store.ChunkCandidate] = []
    skipped = 0
    for chunk in chunks:
        candidate = store.ChunkCandidate(
            category=chunk.category,
            questions=chunk.questions,
            answer=chunk.answer,
            section_path=chunk.section_path,
            content_type=chunk.content_type,
            is_key_clause=chunk.is_key_clause,
        )
        fingerprint = chunk_fingerprint(candidate.questions, candidate.answer)
        if fingerprint in seen:
            skipped += 1
            continue
        seen.add(fingerprint)
        candidates.append(candidate)

    try:
        chunk_ids = _persist_chunks(candidates)
    except Exception as exc:  # noqa: BLE001 - MySQL 不可达统一 502
        raise HTTPException(
            status_code=502, detail=f"MySQL 不可达,写入知识块失败:{exc}"
        ) from exc

    inserted = len(chunk_ids)
    result: dict = {
        "inserted": inserted,
        "skipped": skipped,
        "chunk_ids": chunk_ids,
        "vectorized": None,
        # 未跑向量化时,新写入的行都在 pending。
        "pending": inserted,
    }
    if not payload.vectorize:
        return result

    try:
        vectorized = vectorize_pending()
    except Exception as exc:  # noqa: BLE001 - 已入库,失败只提示补跑
        logger.warning("入库后向量化失败: %s", exc)
        return JSONResponse(
            status_code=502,
            content={
                "detail": f"已入库 {inserted} 块(pending),补跑一次即可",
                "inserted": inserted,
            },
        )

    result["vectorized"] = asdict(vectorized)
    result["pending"] = vectorized.pending_left
    if vectorized.failed > 0:
        logger.warning(
            "入库后有 %d 块向量化失败,仍保持 pending", vectorized.failed
        )
        return JSONResponse(
            status_code=502,
            content={
                "detail": f"已入库 {inserted} 块(pending),补跑一次即可",
                "inserted": inserted,
            },
        )
    return result


@router.post("/vectorize")
def post_vectorize(payload: VectorizeRequest | None = None):
    """向量化 pending 切片;异常或仍有失败行时返回 502。"""
    limit = payload.limit if payload is not None else None
    try:
        result = vectorize_pending(limit)
    except Exception as exc:  # noqa: BLE001 - MySQL 等异常统一 502
        raise HTTPException(
            status_code=502, detail=f"向量化失败:{exc}"
        ) from exc

    body = asdict(result)
    if result.failed > 0:
        return JSONResponse(
            status_code=502,
            content={
                "detail": (
                    f"向量化失败:{result.failed} 块仍为 pending,"
                    "补跑一次即可"
                ),
                **body,
            },
        )
    return body


# ---------------------------------------------------------------------------
# 暂存表 / 检索自测
# ---------------------------------------------------------------------------


@router.get("/staging")
def get_staging(status: str | None = None, limit: int = 50) -> dict:
    """暂存表三态计数 + 按状态筛选的明细。"""
    # 页面可能传空字符串表示「全部」。
    normalized_status = status or None
    try:
        counts = store.staging_counts()
        rows = store.list_staging(normalized_status, limit)
    except Exception as exc:  # noqa: BLE001 - MySQL 不可达统一 502
        raise HTTPException(
            status_code=502, detail=f"MySQL 不可达,读取暂存失败:{exc}"
        ) from exc
    return {"counts": counts, "rows": [asdict(row) for row in rows]}


@router.post("/search")
def post_search(payload: SearchRequest) -> dict:
    """检索自测:只走 dense;阈值以下不返回,Milvus/嵌入失败 502。"""
    threshold = get_settings().knowledge_min_score
    try:
        hits = search_hits(payload.query, payload.top_k)
    except Exception as exc:  # noqa: BLE001 - 不把依赖故障伪装成无匹配
        raise HTTPException(
            status_code=502, detail=f"检索失败:{exc}"
        ) from exc

    accepted = [hit for hit in hits if hit.score >= threshold]
    return {
        "threshold": threshold,
        "hits": [asdict(hit) for hit in accepted],
        "message": None if accepted else "无匹配",
    }
