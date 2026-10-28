"""ch04 非破坏性迁移：原子清单保留基线，MySQL 继续是原文权威源。"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.kb import milvus, store
from app.kb.catalog import infer_product_category
from app.kb.documents import build_chunks, chunk_fingerprint, embedding_text
from app.kb.embedding import embed_texts
from app.kb.sources import get_sources
from app.flywheel.publishing import legacy_mutation

_lock = RLock()


class MigrationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    target_collection: str
    source_hashes: dict[str, str]
    legacy_snapshot_ids: list[int]
    reused_ids: list[int]
    legacy_excluded_ids: list[int]
    phase: Literal["snapshot", "ingested", "indexing", "validated"] = "snapshot"
    completed_ids: list[int] = Field(default_factory=list)


def _sources():
    return get_sources(corpus_version="ch04")


def _hashes() -> dict[str, str]:
    return {str(s.path): hashlib.sha256(s.path.read_bytes()).hexdigest() for s in _sources()}


def _path() -> Path:
    return Path(get_settings().kb_migration_manifest)


def _save(manifest: MigrationManifest) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(manifest.model_dump_json(indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary,path)
                break
            except PermissionError:
                if attempt==5:raise
                time.sleep(.01*(2**attempt))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_manifest() -> MigrationManifest:
    if not _path().is_file():
        raise ValueError("迁移清单缺失；请先 dry-run 并迁移，已有目标集合须恢复原清单")
    manifest = MigrationManifest.model_validate_json(_path().read_text(encoding="utf-8"))
    settings = get_settings()
    if manifest.target_collection != settings.hybrid_collection:
        raise ValueError("迁移清单目标集合不符，禁止重新建立基线")
    if manifest.source_hashes != _hashes():
        raise ValueError("迁移清单 source hash 不符；请恢复匹配的材料/清单")
    snapshot, reused, excluded = map(set, (
        manifest.legacy_snapshot_ids, manifest.reused_ids, manifest.legacy_excluded_ids))
    if reused & excluded or reused | excluded != snapshot:
        raise ValueError("迁移清单历史 ID 分区非法")
    _guard_target(manifest.target_collection)
    return manifest


def _guard_target(target: str) -> None:
    if target == get_settings().knowledge_collection:
        raise ValueError("混合集合必须与旧 knowledge 集合分离")


def _canonical():
    result = []
    seen = set()
    for source in _sources():
        group = []
        for chunk in build_chunks(source.path.read_text(encoding="utf-8"), source):
            fingerprint = chunk_fingerprint(chunk.questions, chunk.answer)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            group.append(store.ChunkCandidate(chunk.category, chunk.questions, chunk.answer,
                         chunk.section_path, chunk.content_type, chunk.is_key_clause))
        result.append(group)
    return result


def _identity(row):
    # 指纹相同但过滤/章节不同的旧块不能作为新版权威块，否则会污染过滤和引用。
    return (chunk_fingerprint(row.questions, row.answer), row.category,
            row.section_path, row.content_type)


def prepare_migration(*, dry_run: bool = True) -> dict:
    with _lock:
        target = get_settings().hybrid_collection
        _guard_target(target)
        if _path().exists():
            manifest = load_manifest()
        else:
            if milvus.get_client().has_collection(target):
                raise ValueError("目标集合存在但迁移清单缺失；请恢复原清单，禁止猜测基线")
            ids = store.all_chunk_ids()
            canonical = {_identity(c) for group in _canonical() for c in group}
            reused = [r.id for r in store.iter_chunks_by_ids(ids) if _identity(r) in canonical]
            manifest = MigrationManifest(target_collection=target, source_hashes=_hashes(),
                legacy_snapshot_ids=ids, reused_ids=reused,
                legacy_excluded_ids=sorted(set(ids) - set(reused)))
            if not dry_run:
                _save(manifest)
        return {"target_collection": target, "mysql_total": len(store.all_chunk_ids()),
                "source_hashes": manifest.source_hashes,
                "legacy_excluded": len(manifest.legacy_excluded_ids),
                "reused": len(manifest.reused_ids), "phase": manifest.phase,
                "dry_run": dry_run}


def eligible_chunk_ids() -> set[int]:
    manifest = load_manifest()
    return set(store.all_chunk_ids()) - set(manifest.legacy_excluded_ids)


def record_for(row: store.StoredChunk) -> dict:
    return {"id": str(row.id),
            "text": embedding_text(row.category, row.questions, row.answer),
            "category": row.category,
            "product_category": infer_product_category(row.section_path or "", row.questions, row.answer),
            "content_type": row.content_type or ""}


def _write_batch(rows: list[store.StoredChunk], target: str) -> None:
    records = [record_for(row) for row in rows]
    if any(len(r["text"].encode("utf-8")) > 65535 for r in records):
        raise ValueError("检索文本超过 Milvus UTF-8 字节上限")
    vectors = embed_texts([r["text"] for r in records])
    if len(vectors) != len(records):
        raise ValueError("嵌入返回数量不匹配")
    milvus.upsert_records([dict(r, vector=v) for r, v in zip(records, vectors)],
                          collection_name=target)
    milvus.get_client().flush(target)
    # 旧 done 块不改元数据；新块只在 flush 成功后标 done。
    store.mark_done([r.id for r in rows if r.vectorize_status != "done"])


@legacy_mutation
def migrate_hybrid() -> dict:
    with _lock:
        prepare_migration(dry_run=False)
        manifest = load_manifest()
        # schema 兼容性必须先于任何 MySQL 写入。
        milvus.ensure_hybrid_collection(manifest.target_collection)
        if manifest.phase == "snapshot":
            with store._session_scope(None) as session:
                existing = {_identity(r) for r in store.iter_chunks_by_ids(store.all_chunk_ids())}
                for group in _canonical():
                    candidates = [c for c in group if _identity(c) not in existing]
                    ids = store.insert_chunks(candidates, session=session)
                    store.link_siblings(ids, session=session)
                    existing.update(_identity(c) for c in candidates)
            manifest.phase = "ingested"
            _save(manifest)
        manifest.phase = "indexing"
        _save(manifest)
        # 不相信全库 done 标志或旧 completed_ids：重跑也精确回填目标，可恢复丢失的向量。
        batch = []
        for row in store.iter_chunks_by_ids(eligible_chunk_ids()):
            batch.append(row)
            if len(batch) >= max(1, get_settings().embedding_batch_size):
                _write_batch(batch, manifest.target_collection)
                manifest.completed_ids = sorted(set(manifest.completed_ids) | {r.id for r in batch})
                _save(manifest)
                batch = []
        if batch:
            _write_batch(batch, manifest.target_collection)
            manifest.completed_ids = sorted(set(manifest.completed_ids) | {r.id for r in batch})
            _save(manifest)
        report = verify_hybrid()
        if report["status"] != "consistent":
            raise RuntimeError("混合集合校验未通过，清单保留 indexing 状态")
        manifest.phase = "validated"
        _save(manifest)
        return report


def verify_hybrid() -> dict:
    manifest = load_manifest()
    ids = eligible_chunk_ids()
    expected = {str(r.id): record_for(r) for r in store.iter_chunks_by_ids(ids)}
    present = milvus.get_client().has_collection(manifest.target_collection)
    actual = {str(r["id"]): r for r in milvus.iter_records(manifest.target_collection)} if present else {}
    missing, orphan = sorted(expected.keys() - actual.keys()), sorted(actual.keys() - expected.keys())
    stale = sorted(key for key in expected.keys() & actual.keys()
                   if any(actual[key].get(field) != value for field, value in expected[key].items()))
    pending = sum(r.vectorize_status != "done" for r in store.iter_chunks_by_ids(ids))
    return {"status": "mismatch" if missing or orphan or stale or pending else "consistent",
            "mysql_total": len(store.all_chunk_ids()), "eligible_total": len(ids),
            "legacy_excluded": len(manifest.legacy_excluded_ids),
            "eligible_pending": pending, "eligible_done": len(ids) - pending,
            "target_count": len(actual), "missing_in_milvus": missing,
            "orphan_in_milvus": orphan, "stale_ids": stale}


@legacy_mutation
def vectorize_hybrid(*, pending_only: bool = True, limit: int | None = None) -> tuple[int, int, int]:
    """补漏/恢复只触及 eligible；恢复幂等覆盖新集合，不删除集合或重置旧行。"""
    manifest = load_manifest()
    milvus.ensure_hybrid_collection(manifest.target_collection)
    rows = [r for r in store.iter_chunks_by_ids(eligible_chunk_ids())
            if not pending_only or r.vectorize_status == "pending"]
    if limit is not None:
        rows = rows[:max(0, limit)]
    processed = failed = 0
    size = max(1, get_settings().embedding_batch_size)
    for start in range(0, len(rows), size):
        batch = rows[start:start + size]
        try:
            _write_batch(batch, manifest.target_collection)
        except Exception:
            # 调用方只收到失败数量，不把可能含凭据的远端异常写进页面日志。
            failed += len(batch)
        else:
            processed += len(batch)
    pending = sum(r.vectorize_status == "pending"
                  for r in store.iter_chunks_by_ids(eligible_chunk_ids()))
    return processed, failed, pending
