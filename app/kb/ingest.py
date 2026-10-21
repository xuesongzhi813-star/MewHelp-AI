"""知识库建库 CLI。

职责:
- ``preview``:只读预览三份建库材料的切块结果,不写库;
- ``docs`` / ``faq``:按内容指纹幂等录入 Markdown 文档与 FAQ 表;
- ``vectorize`` / ``rebuild``:增量向量化 / 全量重建向量索引;
- ``build``:文档 + FAQ 录入后立即向量化;
- ``status``:汇总 MySQL 计数、Milvus 可达性与双写一致性;
- ``reset``:破坏性清空 MySQL 切片与 Milvus 集合,必须显式传 ``--yes``。

写入路径统一在单个事务内完成(传入 session 给仓储层,由本模块提交),
任一步失败都不会留下半份材料;向量化则沿用 ``dualwrite`` 的断点续跑语义。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_local
from app.kb import dualwrite, milvus, store
from app.kb.documents import Chunk, Source, build_chunks, chunk_fingerprint
from app.kb.dualwrite import VectorizeResult, vectorize_pending
from app.kb.sources import get_sources
from app.models import Faq

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    """一轮录入的结果;``chunk_ids`` 与 ``inserted`` 长度一致。"""

    inserted: int
    skipped: int
    chunk_ids: tuple[int, ...]


# ---------------------------------------------------------------------------
# 只读预览
# ---------------------------------------------------------------------------


def _read_source(source: Source) -> str:
    """按 UTF-8 读取一份建库材料。"""
    return source.path.read_text(encoding="utf-8")


def run_preview(*, corpus_version: str | None = None) -> list[dict]:
    """返回每份材料的切块统计,不产生任何数据库或 Milvus 写入。"""
    preview: list[dict] = []
    for source in (get_sources() if corpus_version is None else get_sources(corpus_version=corpus_version)):
        chunks = build_chunks(_read_source(source), source)
        preview.append(
            {
                "path": str(source.path),
                "title": source.title,
                "content_type": source.content_type,
                "chunks": len(chunks),
                "key_clauses": sum(1 for chunk in chunks if chunk.is_key_clause),
                "features": sorted(
                    {feature for chunk in chunks for feature in chunk.features}
                ),
            }
        )
    return preview


# ---------------------------------------------------------------------------
# 录入
# ---------------------------------------------------------------------------


def _candidate_from_chunk(chunk: Chunk) -> store.ChunkCandidate:
    """把切块器的 :class:`~app.kb.documents.Chunk` 转成仓储候选。"""
    return store.ChunkCandidate(
        category=chunk.category,
        questions=chunk.questions,
        answer=chunk.answer,
        section_path=chunk.section_path,
        content_type=chunk.content_type,
        is_key_clause=chunk.is_key_clause,
    )


def ingest_docs() -> IngestResult:
    """把 ``get_sources()`` 的 Markdown 按指纹幂等录入。

    同一轮内新插入的指纹也会并入 ``seen``,避免多份材料里的重复内容
    被连续插入。每份材料的新块单独串联前后指针。
    """
    if get_settings().kb_corpus_version == "ch04":
        from app.kb.migration import load_manifest
        load_manifest()
    inserted_ids: list[int] = []
    skipped = 0

    with get_session_local()() as session:
        with session.begin():
            seen = store.existing_fingerprints(session=session)
            for source in get_sources():
                chunks = build_chunks(_read_source(source), source)
                candidates: list[store.ChunkCandidate] = []
                skipped_here = 0
                for chunk in chunks:
                    candidate = _candidate_from_chunk(chunk)
                    fingerprint = chunk_fingerprint(
                        candidate.questions, candidate.answer
                    )
                    if fingerprint in seen:
                        skipped_here += 1
                        continue
                    seen.add(fingerprint)
                    candidates.append(candidate)

                ids = store.insert_chunks(candidates, session=session)
                # 前后指针只在同一份材料内部串联;跳过已存在块后,
                # 新插入部分自然成链,不会指向旧块。
                store.link_siblings(ids, session=session)
                inserted_ids.extend(ids)
                skipped += skipped_here
                if ids and skipped_here:
                    logger.warning(
                        "%s: 部分块已存在,前后块指针只覆盖新插入部分"
                        "(新插入 %d 块,跳过 %d 块)",
                        source.title,
                        len(ids),
                        skipped_here,
                    )

    return IngestResult(
        inserted=len(inserted_ids),
        skipped=skipped,
        chunk_ids=tuple(inserted_ids),
    )


def ingest_faq() -> IngestResult:
    """把 ``faq`` 表全部行按指纹幂等录入为知识切片。"""
    if get_settings().kb_corpus_version == "ch04":
        raise ValueError("ch04 不收录旧 faq 种子；请使用 migrate-hybrid 或 docs")
    inserted_ids: list[int] = []
    skipped = 0

    with get_session_local()() as session:
        with session.begin():
            rows = session.scalars(select(Faq).order_by(Faq.id)).all()
            seen = store.existing_fingerprints(session=session)
            candidates: list[store.ChunkCandidate] = []
            for row in rows:
                candidate = store.ChunkCandidate(
                    category=row.category,
                    questions=row.question,
                    answer=row.answer,
                    section_path=f"商品FAQ > {row.category}",
                    content_type="商品FAQ",
                    is_key_clause=False,
                )
                fingerprint = chunk_fingerprint(
                    candidate.questions, candidate.answer
                )
                if fingerprint in seen:
                    skipped += 1
                    continue
                seen.add(fingerprint)
                candidates.append(candidate)
            inserted_ids = store.insert_chunks(candidates, session=session)

    return IngestResult(
        inserted=len(inserted_ids),
        skipped=skipped,
        chunk_ids=tuple(inserted_ids),
    )


def build_all() -> IngestResult:
    """按 docs → faq → vectorize 的顺序完成一轮建库。

    向量化沿用 ``dualwrite`` 的失败语义(单批失败只累计 ``failed``,
    不抛异常)。因此本函数必须检查返回值:只要有失败或剩余 pending,
    就告警并抛 ``RuntimeError``,让 CLI / 任务显式失败;MySQL 中未完成
    的行保持 pending,补跑 ``vectorize`` 即可收敛。
    """
    docs = ingest_docs()
    faq = ingest_faq() if get_settings().kb_corpus_version != "ch04" else IngestResult(0, 0, ())
    result = IngestResult(
        inserted=docs.inserted + faq.inserted,
        skipped=docs.skipped + faq.skipped,
        chunk_ids=docs.chunk_ids + faq.chunk_ids,
    )

    vectorized = vectorize_pending()
    if vectorized.failed > 0 or vectorized.pending_left > 0:
        logger.warning(
            "建库后向量化未完成:processed=%d, failed=%d, pending_left=%d",
            vectorized.processed,
            vectorized.failed,
            vectorized.pending_left,
        )
        raise RuntimeError(
            f"已入库 {result.inserted} 块,但向量化未完成"
            f"(failed={vectorized.failed}, pending_left={vectorized.pending_left}),"
            "补跑 vectorize 即可"
        )
    return result


# ---------------------------------------------------------------------------
# 向量化 / 状态 / 重置
# ---------------------------------------------------------------------------


def rebuild() -> VectorizeResult:
    """保留 MySQL 原文,重置全部 pending 并重刷 Milvus。"""
    return dualwrite.rebuild_all()


def status_report() -> dict:
    """汇总 MySQL 计数、Milvus 可用性与一致性快照。

    Milvus 不可达时 ``milvus_ok=False`` 且 ``milvus_count=None``,
    绝不把「读不到」误报成 0。服务可达但集合尚未创建时仍算可用:
    ``milvus_ok=True``、``milvus_count=0``、``milvus_note="集合未创建"``。
    """
    report: dict = dict(store.overview_counts())
    if get_settings().kb_corpus_version == "ch04":
        from app.kb.migration import verify_hybrid
        try:
            scoped = verify_hybrid()
        except Exception:
            report.update(milvus_ok=False, milvus_count=None, milvus_note="混合集合或迁移清单读不到",
                          consistency="unknown", missing_in_milvus=(), orphan_in_milvus=(),
                          consistency_note="尚未完成范围校验")
        else:
            report.update(scoped)
            report.update(milvus_ok=True, milvus_count=scoped["target_count"], milvus_note=None,
                          consistency=scoped["status"], consistency_note=
                          f"逐 ID/文本/元数据比对；内容不符 {len(scoped['stale_ids'])} 条")
        return report

    milvus_ok = False
    milvus_count: int | None = None
    milvus_note: str | None = None
    try:
        milvus_ok = bool(milvus.ping())
    except Exception as exc:  # noqa: BLE001 - 可用性探测失败一律不可用
        logger.warning("Milvus 可用性探测失败: %s", exc)
        milvus_ok = False

    if milvus_ok:
        try:
            collection_exists = bool(milvus.has_collection())
        except Exception as exc:  # noqa: BLE001 - 读不到集合状态视为不可用
            logger.warning("读取 Milvus 集合状态失败: %s", exc)
            milvus_ok = False
            collection_exists = False
        if milvus_ok and not collection_exists:
            milvus_count = 0
            milvus_note = "集合未创建"
        elif milvus_ok:
            try:
                milvus_count = milvus.count()
            except Exception as exc:  # noqa: BLE001 - 读不到数量视为不可用
                logger.warning("读取 Milvus 数量失败: %s", exc)
                milvus_ok = False
                milvus_count = None

    snapshot = dualwrite.consistency_snapshot()
    report.update(
        {
            "milvus_ok": milvus_ok,
            "milvus_count": milvus_count,
            "milvus_note": milvus_note,
            "consistency": snapshot.status,
            "missing_in_milvus": snapshot.missing_in_milvus,
            "orphan_in_milvus": snapshot.orphan_in_milvus,
            "consistency_note": snapshot.note,
        }
    )
    return report


def reset_all(force: bool) -> None:
    """破坏性清空知识库;``force=False`` 时拒绝执行。"""
    if not force:
        raise ValueError("reset 是破坏性操作,必须显式传 --yes")
    dualwrite.reset_all()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_ingest(label: str, result: IngestResult) -> None:
    print(
        f"{label}:新增 {result.inserted} 块,跳过 {result.skipped} 块"
        f"(共处理 {result.inserted + result.skipped} 块)。"
    )


def _print_vectorize(label: str, result: VectorizeResult) -> None:
    print(
        f"{label}:成功 {result.processed} 块,失败 {result.failed} 块,"
        f"剩余待处理 {result.pending_left} 块。"
    )


def _vectorize_exit_code(result: VectorizeResult) -> int:
    """向量化有失败或剩余 pending 时返回 1,让页面作业显示 failed。"""
    return 1 if result.failed > 0 or result.pending_left > 0 else 0


def _command_vectorize() -> int:
    result = vectorize_pending()
    _print_vectorize("向量化完成", result)
    return _vectorize_exit_code(result)


def _command_rebuild() -> int:
    result = rebuild()
    _print_vectorize("向量重建完成", result)
    return _vectorize_exit_code(result)


def _command_preview(corpus_version: str | None = None) -> None:
    preview = run_preview() if corpus_version is None else run_preview(corpus_version=corpus_version)
    for item in preview:
        features = "、".join(item["features"]) or "无"
        print(
            f"{item['title']} | {item['path']} | {item['content_type']} | "
            f"{item['chunks']} 块 | 关键条款 {item['key_clauses']} 条 | "
            f"切分特征:{features}"
        )
    total = sum(item["chunks"] for item in preview)
    print(f"预览完成:共 {len(preview)} 份材料、{total} 块,未写入数据库。")


def _command_status() -> None:
    report = status_report()
    print("知识库状态:")
    print(f"  切片总数:{report['chunks']}")
    print(f"  待向量化:{report['pending']}")
    print(f"  已向量化:{report['done']}")
    print(f"  关键条款:{report['key_clauses']}")
    for key in ("mysql_total", "eligible_total", "legacy_excluded", "eligible_pending", "eligible_done", "target_count"):
        if key in report:
            print(f"  {key}:{report[key]}")
    print(f"  Milvus:{'可用' if report['milvus_ok'] else '不可用'}")
    if report.get("milvus_note"):
        print(f"  Milvus 说明:{report['milvus_note']}")
    count = report["milvus_count"]
    print(f"  Milvus 向量数:{count if count is not None else '未知'}")
    print(f"  一致性:{report['consistency']}({report['consistency_note']})")
    if report["missing_in_milvus"]:
        print(f"  MySQL 有、Milvus 缺失:{', '.join(report['missing_in_milvus'])}")
    if report["orphan_in_milvus"]:
        print(f"  Milvus 多余:{', '.join(report['orphan_in_milvus'])}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.kb.ingest",
        description="MewHelp 知识库建库工具",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview_parser = subparsers.add_parser("preview", help="只预览切块结果,不写库")
    preview_parser.add_argument("--corpus-version", choices=["ch03", "ch04"])
    migration_parser = subparsers.add_parser("migrate-hybrid", help="按固定清单迁移到新混合集合，保留旧库")
    migration_parser.add_argument("--dry-run", action="store_true", help="只读核对范围，不创建清单或写库")
    subparsers.add_parser("verify-hybrid", help="逐 ID/原文/元数据核对混合集合")
    subparsers.add_parser("docs", help="按指纹幂等录入 Markdown 文档")
    subparsers.add_parser("faq", help="按指纹幂等录入 faq 表")
    subparsers.add_parser("vectorize", help="向量化所有 pending 切片")
    subparsers.add_parser("rebuild", help="保留原文,重建 Milvus 向量索引")
    subparsers.add_parser("build", help="录入文档与 FAQ 后立即向量化")
    subparsers.add_parser("status", help="查看 MySQL/Milvus 状态与一致性")

    reset_parser = subparsers.add_parser(
        "reset", help="破坏性清空 MySQL 切片与 Milvus 集合"
    )
    reset_parser.add_argument(
        "--yes",
        action="store_true",
        help="确认执行破坏性重置",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口:成功返回 0,异常打印中文错误到 stderr 并返回 1。"""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "preview":
            _command_preview(args.corpus_version)
        elif args.command == "migrate-hybrid":
            from app.kb.migration import prepare_migration, migrate_hybrid
            report = prepare_migration() if args.dry_run else migrate_hybrid()
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "verify-hybrid":
            from app.kb.migration import verify_hybrid
            report = verify_hybrid()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["status"] == "consistent" else 1
        elif args.command == "docs":
            _print_ingest("文档录入完成", ingest_docs())
        elif args.command == "faq":
            _print_ingest("FAQ 录入完成", ingest_faq())
        elif args.command == "vectorize":
            return _command_vectorize()
        elif args.command == "rebuild":
            return _command_rebuild()
        elif args.command == "build":
            _print_ingest("建库完成", build_all())
        elif args.command == "status":
            _command_status()
        elif args.command == "reset":
            reset_all(force=args.yes)
            print("知识库已重置:MySQL 切片与 Milvus 集合均已清空。")
    except Exception as exc:  # noqa: BLE001 - CLI 统一转成退出码 1
        print(f"错误:{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
