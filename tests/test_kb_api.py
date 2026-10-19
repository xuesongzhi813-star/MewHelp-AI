"""``/api/kb`` 路由测试(Task 13)。

全部用例用 ``httpx.ASGITransport`` 打 ``app.main.app``,通过 monkeypatch 替换
store / dualwrite / milvus / retriever / jobs 的边界函数,不连真实 MySQL、
Milvus 或嵌入 API。覆盖:
- overview 七块结构,以及 MySQL / Milvus 降级语义;
- source 白名单与任意路径读取防护;
- preview 纯 dry-run(不写库、不调检索/向量化);
- ingest 的指纹跳过、413、MySQL 502、向量化失败 502(不回滚);
- vectorize / staging / search 的正常与失败路径。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

import app.api.kb as kb_api
from app.kb.documents import Source, build_chunks, chunk_fingerprint
from app.kb.dualwrite import Consistency, VectorizeResult
from app.kb.retriever import Hit
from app.kb.store import StagingRow, StoredChunk


# ---------------------------------------------------------------------------
# 假数据与辅助
# ---------------------------------------------------------------------------


def _stored(chunk_id: int, *, status: str = "pending") -> StoredChunk:
    return StoredChunk(
        id=chunk_id,
        category="测试 > 章节",
        questions="问",
        answer="答",
        section_path="测试 > 章节",
        content_type="文档章节",
        is_key_clause=False,
        vectorize_status=status,
        vector_id=str(chunk_id) if status == "done" else None,
        prev_chunk_id=None,
        next_chunk_id=None,
        created_at=datetime(2026, 10, 19, 8, 30, tzinfo=timezone.utc),
    )


def _hit(chunk_id: int, score: float) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        score=score,
        category="售后政策",
        questions=f"问题{chunk_id}",
        answer=f"答案{chunk_id}",
        content_type="文档章节",
        section_path="售后政策 > 退货",
    )


def _db_down(*_args, **_kwargs):
    raise OperationalError("SELECT 1", {}, Exception("mysql down"))


def _patch_settings(monkeypatch, **overrides) -> None:
    values = {
        "kb_max_markdown_bytes": 204800,
        "knowledge_collection": "knowledge",
        "knowledge_min_score": 0.5,
    }
    values.update(overrides)
    monkeypatch.setattr(kb_api, "get_settings", lambda: SimpleNamespace(**values))


def _patch_healthy_infra(monkeypatch) -> None:
    """把 overview 需要的 MySQL / Milvus / jobs 全部换成健康假实现。"""
    monkeypatch.setattr(
        kb_api.store,
        "overview_counts",
        lambda: {"chunks": 5, "pending": 2, "done": 3, "key_clauses": 1},
    )
    monkeypatch.setattr(
        kb_api.store,
        "staging_counts",
        lambda: {"extracted": 4, "kept": 1, "discarded": 2, "batches": 3},
    )
    monkeypatch.setattr(
        kb_api.store, "list_recent", lambda limit=20: [_stored(9, status="done")]
    )
    monkeypatch.setattr(kb_api.milvus, "ping", lambda: True)
    monkeypatch.setattr(kb_api.milvus, "count", lambda: 42)
    monkeypatch.setattr(
        kb_api,
        "consistency_snapshot",
        lambda: Consistency("consistent", (), (), "已完成精确 id 比对"),
    )
    monkeypatch.setattr(
        kb_api,
        "list_jobs",
        lambda: [
            {
                "name": "kb-preview",
                "target": "kb-preview",
                "heavy": False,
                "status": "idle",
            }
        ],
    )
    _patch_settings(monkeypatch)


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


async def test_overview_returns_seven_blocks(client, monkeypatch):
    _patch_healthy_infra(monkeypatch)

    response = await client.get("/api/kb/overview")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "mysql",
        "milvus",
        "consistency",
        "staging",
        "sources",
        "recent",
        "jobs",
        "jobs_ok",
    }
    assert body["mysql"] == {
        "ok": True,
        "chunks": 5,
        "pending": 2,
        "done": 3,
        "key_clauses": 1,
    }
    assert body["milvus"] == {"ok": True, "count": 42, "collection": "knowledge"}
    assert body["consistency"]["status"] == "consistent"
    assert body["staging"] == {
        "extracted": 4,
        "kept": 1,
        "discarded": 2,
        "batches": 3,
    }
    assert body["recent"][0]["id"] == 9
    assert body["recent"][0]["vectorize_status"] == "done"
    # spec §16:recent[] 必须带 created_at。
    assert body["recent"][0]["created_at"] is not None
    assert body["recent"][0]["created_at"].startswith("2026-10-19")
    assert body["jobs"][0]["name"] == "kb-preview"
    assert body["jobs_ok"] is True
    # sources 始终现场切分,三份样例材料都应可读。
    assert len(body["sources"]) == 3
    assert all(item["chunks"] > 0 for item in body["sources"])
    assert all("error" not in item for item in body["sources"])


async def test_overview_degrades_when_mysql_down(client, monkeypatch):
    monkeypatch.setattr(kb_api.store, "overview_counts", _db_down)
    monkeypatch.setattr(kb_api.store, "staging_counts", _db_down)
    monkeypatch.setattr(kb_api.store, "list_recent", _db_down)
    monkeypatch.setattr(kb_api.milvus, "ping", lambda: True)
    monkeypatch.setattr(kb_api.milvus, "count", lambda: 42)
    monkeypatch.setattr(
        kb_api,
        "consistency_snapshot",
        lambda: Consistency("unknown", (), (), "MySQL 读不到"),
    )
    monkeypatch.setattr(kb_api, "list_jobs", lambda: [])
    _patch_settings(monkeypatch)

    response = await client.get("/api/kb/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["mysql"] == {
        "ok": False,
        "chunks": None,
        "pending": None,
        "done": None,
        "key_clauses": None,
    }
    assert body["staging"] is None
    assert body["recent"] == []
    # MySQL 挂了不影响材料清单与作业字段。
    assert len(body["sources"]) == 3
    assert all(item["chunks"] > 0 for item in body["sources"])
    assert body["jobs"] == []
    assert body["jobs_ok"] is True


async def test_overview_consistency_unknown_when_milvus_down(client, monkeypatch):
    monkeypatch.setattr(
        kb_api.store,
        "overview_counts",
        lambda: {"chunks": 1, "pending": 0, "done": 1, "key_clauses": 0},
    )
    monkeypatch.setattr(
        kb_api.store,
        "staging_counts",
        lambda: {"extracted": 0, "kept": 0, "discarded": 0, "batches": 0},
    )
    monkeypatch.setattr(kb_api.store, "list_recent", lambda limit=20: [])
    monkeypatch.setattr(kb_api.milvus, "ping", lambda: False)
    monkeypatch.setattr(kb_api.milvus, "count", _db_down)
    monkeypatch.setattr(kb_api, "list_jobs", lambda: [])
    _patch_settings(monkeypatch)
    # 不替换 consistency_snapshot:走真实的 dualwrite 探测,验证 Milvus 挂时
    # 一定是 unknown 而不是 mismatch。

    response = await client.get("/api/kb/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["milvus"] == {
        "ok": False,
        "count": None,
        "collection": "knowledge",
    }
    assert body["consistency"]["status"] == "unknown"
    assert body["consistency"]["missing_in_milvus"] == []
    assert body["consistency"]["orphan_in_milvus"] == []


async def test_overview_source_read_error_is_isolated(client, monkeypatch, tmp_path):
    good_path = tmp_path / "good.md"
    good_path.write_text("# 标题\n\n正文。", encoding="utf-8")
    good = Source(good_path, "正常", "文档章节")
    bad = Source(tmp_path / "missing.md", "缺失", "文档章节")
    monkeypatch.setattr(kb_api, "get_sources", lambda: [good, bad])

    monkeypatch.setattr(
        kb_api.store,
        "overview_counts",
        lambda: {"chunks": 1, "pending": 0, "done": 1, "key_clauses": 0},
    )
    monkeypatch.setattr(
        kb_api.store,
        "staging_counts",
        lambda: {"extracted": 0, "kept": 0, "discarded": 0, "batches": 0},
    )
    monkeypatch.setattr(kb_api.store, "list_recent", lambda limit=20: [])
    monkeypatch.setattr(kb_api.milvus, "ping", lambda: True)
    monkeypatch.setattr(kb_api.milvus, "count", lambda: 1)
    monkeypatch.setattr(
        kb_api, "consistency_snapshot", lambda: Consistency("consistent", (), (), "")
    )
    monkeypatch.setattr(kb_api, "list_jobs", lambda: [])
    _patch_settings(monkeypatch)

    response = await client.get("/api/kb/overview")

    assert response.status_code == 200
    sources = response.json()["sources"]
    assert sources[0]["chunks"] == 1
    assert "error" not in sources[0]
    assert sources[1]["chunks"] == 0
    assert sources[1]["key_clauses"] == 0
    assert sources[1]["features"] == []
    assert sources[1]["error"] == "读不到"


async def test_overview_jobs_unavailable_sets_jobs_ok_false(client, monkeypatch):
    _patch_healthy_infra(monkeypatch)
    monkeypatch.setattr(kb_api, "list_jobs", _db_down)

    response = await client.get("/api/kb/overview")

    assert response.status_code == 200
    assert response.json()["jobs"] == []
    assert response.json()["jobs_ok"] is False


# ---------------------------------------------------------------------------
# source
# ---------------------------------------------------------------------------


async def test_source_whitelist_allows_declared_path(client):
    response = await client.get(
        "/api/kb/source", params={"path": "data/kb/退货政策.md"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["path"].endswith("data/kb/退货政策.md")
    assert body["title"] == "退货政策"
    assert body["content_type"] == "文档章节"
    assert body["markdown"].strip()


async def test_source_rejects_paths_outside_whitelist(client, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("机密", encoding="utf-8")

    for bad_path in (
        "C:/Windows/win.ini",
        str(outside),
        "data/kb/不存在.md",
        "data/kb/../kb/退货政策.md",
    ):
        response = await client.get("/api/kb/source", params={"path": bad_path})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


async def test_preview_is_dry_run(client, monkeypatch):
    boom_calls: list[str] = []

    def boom(*_args, **_kwargs):
        boom_calls.append("called")
        raise AssertionError("preview 不得触达存储/向量化")

    monkeypatch.setattr(kb_api.store, "overview_counts", boom)
    monkeypatch.setattr(kb_api.store, "insert_chunks", boom)
    monkeypatch.setattr(kb_api.store, "staging_counts", boom)
    monkeypatch.setattr(kb_api.milvus, "ping", boom)
    monkeypatch.setattr(kb_api, "vectorize_pending", boom)
    monkeypatch.setattr(kb_api, "search_hits", boom)
    _patch_settings(monkeypatch)

    response = await client.post(
        "/api/kb/preview",
        json={
            "markdown": "# 测试\n\n正文。\n\n**重要:注意保管。**",
            "source_title": "测试",
            "content_type": "文档章节",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["chunks"] == 1
    assert body["counts"]["key_clauses"] == 1
    assert isinstance(body["features"], list)
    assert body["chunks"][0]["questions"] == "测试"
    assert "正文。" in body["chunks"][0]["answer"]
    assert body["chunks"][0]["content_type"] == "文档章节"
    assert boom_calls == []


async def test_preview_rejects_oversized_markdown(client, monkeypatch):
    _patch_settings(monkeypatch)

    response = await client.post(
        "/api/kb/preview",
        json={
            "markdown": "x" * 204801,
            "source_title": "大",
            "content_type": "文档章节",
        },
    )

    assert response.status_code == 413


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


async def test_ingest_skips_existing_fingerprints(client, monkeypatch):
    markdown = "# 第一节\n\n第一段。\n\n# 第二节\n\n第二段。"
    source = Source(Path("inline.md"), "测试", "文档章节")
    duplicate = build_chunks(markdown, source)[1]
    duplicate_fp = chunk_fingerprint(duplicate.questions, duplicate.answer)

    captured: list = []
    monkeypatch.setattr(kb_api, "_existing_fingerprints", lambda: {duplicate_fp})

    def fake_persist(candidates):
        captured.extend(candidates)
        return [7]

    monkeypatch.setattr(kb_api, "_persist_chunks", fake_persist)
    monkeypatch.setattr(
        kb_api,
        "vectorize_pending",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("未勾选向量化时不得调用")
        ),
    )
    _patch_settings(monkeypatch)

    response = await client.post(
        "/api/kb/ingest",
        json={
            "markdown": markdown,
            "source_title": "测试",
            "content_type": "文档章节",
            "vectorize": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "inserted": 1,
        "skipped": 1,
        "chunk_ids": [7],
        "vectorized": None,
        "pending": 1,
    }
    assert len(captured) == 1
    assert captured[0].questions == "第一节"


async def test_ingest_vectorize_success_returns_counts(client, monkeypatch):
    monkeypatch.setattr(kb_api, "_existing_fingerprints", lambda: set())
    monkeypatch.setattr(kb_api, "_persist_chunks", lambda candidates: [11, 12])
    monkeypatch.setattr(
        kb_api,
        "vectorize_pending",
        lambda limit=None: VectorizeResult(processed=2, failed=0, pending_left=0),
    )
    _patch_settings(monkeypatch)

    response = await client.post(
        "/api/kb/ingest",
        json={
            "markdown": "# 测试\n\n正文。",
            "source_title": "测试",
            "content_type": "文档章节",
            "vectorize": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inserted"] == 2
    assert body["chunk_ids"] == [11, 12]
    assert body["vectorized"] == {
        "processed": 2,
        "failed": 0,
        "pending_left": 0,
    }
    assert body["pending"] == 0


async def test_ingest_vectorize_failure_returns_502_with_inserted(
    client, monkeypatch
):
    persisted: list[int] = []
    monkeypatch.setattr(kb_api, "_existing_fingerprints", lambda: set())

    def fake_persist(candidates):
        persisted.extend([1, 2])
        return [1, 2]

    monkeypatch.setattr(kb_api, "_persist_chunks", fake_persist)
    monkeypatch.setattr(
        kb_api,
        "vectorize_pending",
        lambda limit=None: (_ for _ in ()).throw(RuntimeError("milvus down")),
    )
    _patch_settings(monkeypatch)

    response = await client.post(
        "/api/kb/ingest",
        json={
            "markdown": "# 测试\n\n正文。",
            "source_title": "测试",
            "content_type": "文档章节",
            "vectorize": True,
        },
    )

    assert response.status_code == 502
    body = response.json()
    assert body["inserted"] == 2
    assert "pending" in body["detail"]
    assert "补跑一次即可" in body["detail"]
    # 失败不回滚:写入函数已被调用。
    assert persisted == [1, 2]


async def test_ingest_vectorize_reported_failed_returns_502(client, monkeypatch):
    monkeypatch.setattr(kb_api, "_existing_fingerprints", lambda: set())
    monkeypatch.setattr(kb_api, "_persist_chunks", lambda candidates: [5])
    monkeypatch.setattr(
        kb_api,
        "vectorize_pending",
        lambda limit=None: VectorizeResult(processed=0, failed=1, pending_left=1),
    )
    _patch_settings(monkeypatch)

    response = await client.post(
        "/api/kb/ingest",
        json={
            "markdown": "# 测试\n\n正文。",
            "source_title": "测试",
            "content_type": "文档章节",
            "vectorize": True,
        },
    )

    assert response.status_code == 502
    assert response.json()["inserted"] == 1


async def test_markdown_too_large_returns_413(client, monkeypatch):
    calls: list[str] = []

    def boom(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("超限请求不得触达存储")

    monkeypatch.setattr(kb_api, "_existing_fingerprints", boom)
    monkeypatch.setattr(kb_api, "_persist_chunks", boom)
    _patch_settings(monkeypatch)

    response = await client.post(
        "/api/kb/ingest",
        json={
            "markdown": "x" * 204801,
            "source_title": "大",
            "content_type": "文档章节",
        },
    )

    assert response.status_code == 413
    assert "204800" in response.json()["detail"]
    assert calls == []


async def test_ingest_mysql_failure_returns_502(client, monkeypatch):
    monkeypatch.setattr(kb_api, "_existing_fingerprints", _db_down)
    _patch_settings(monkeypatch)

    response = await client.post(
        "/api/kb/ingest",
        json={
            "markdown": "# 测试\n\n正文。",
            "source_title": "测试",
            "content_type": "文档章节",
        },
    )

    assert response.status_code == 502
    assert "MySQL" in response.json()["detail"]


# ---------------------------------------------------------------------------
# vectorize
# ---------------------------------------------------------------------------


async def test_vectorize_returns_counts_and_passes_limit(client, monkeypatch):
    captured: list[int | None] = []

    def fake_vectorize(limit=None):
        captured.append(limit)
        return VectorizeResult(processed=3, failed=0, pending_left=4)

    monkeypatch.setattr(kb_api, "vectorize_pending", fake_vectorize)
    _patch_settings(monkeypatch)

    response = await client.post("/api/kb/vectorize", json={"limit": 5})

    assert response.status_code == 200
    assert response.json() == {"processed": 3, "failed": 0, "pending_left": 4}
    assert captured == [5]


async def test_vectorize_accepts_empty_body(client, monkeypatch):
    captured: list[int | None] = []

    def fake_vectorize(limit=None):
        captured.append(limit)
        return VectorizeResult(processed=0, failed=0, pending_left=0)

    monkeypatch.setattr(kb_api, "vectorize_pending", fake_vectorize)
    _patch_settings(monkeypatch)

    response = await client.post("/api/kb/vectorize")

    assert response.status_code == 200
    assert captured == [None]


async def test_vectorize_failure_returns_502(client, monkeypatch):
    monkeypatch.setattr(
        kb_api,
        "vectorize_pending",
        lambda limit=None: (_ for _ in ()).throw(RuntimeError("milvus down")),
    )
    _patch_settings(monkeypatch)

    response = await client.post("/api/kb/vectorize", json={})

    assert response.status_code == 502
    assert "milvus down" in response.json()["detail"]


async def test_vectorize_reported_failed_returns_502(client, monkeypatch):
    monkeypatch.setattr(
        kb_api,
        "vectorize_pending",
        lambda limit=None: VectorizeResult(processed=0, failed=2, pending_left=2),
    )
    _patch_settings(monkeypatch)

    response = await client.post("/api/kb/vectorize", json={})

    assert response.status_code == 502
    body = response.json()
    assert body["failed"] == 2
    assert body["pending_left"] == 2


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------


async def test_staging_returns_counts_and_rows(client, monkeypatch):
    calls: list[tuple[str | None, int]] = []
    monkeypatch.setattr(
        kb_api.store,
        "staging_counts",
        lambda: {"extracted": 2, "kept": 1, "discarded": 0, "batches": 1},
    )

    def fake_list(status=None, limit=50):
        calls.append((status, limit))
        return [
            StagingRow(
                id=3,
                batch_no="batch-1",
                source_ref="conv:1:msgs:1-2",
                question="问",
                answer="答",
                status="extracted",
                created_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
            )
        ]

    monkeypatch.setattr(kb_api.store, "list_staging", fake_list)

    response = await client.get(
        "/api/kb/staging", params={"status": "extracted", "limit": 10}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {
        "extracted": 2,
        "kept": 1,
        "discarded": 0,
        "batches": 1,
    }
    assert body["rows"][0]["id"] == 3
    assert body["rows"][0]["batch_no"] == "batch-1"
    assert calls == [("extracted", 10)]


async def test_staging_defaults_to_fifty_and_no_status_filter(client, monkeypatch):
    calls: list[tuple[str | None, int]] = []
    monkeypatch.setattr(
        kb_api.store,
        "staging_counts",
        lambda: {"extracted": 0, "kept": 0, "discarded": 0, "batches": 0},
    )
    monkeypatch.setattr(
        kb_api.store,
        "list_staging",
        lambda status=None, limit=50: (
            calls.append((status, limit)) or []
        ),
    )

    response = await client.get("/api/kb/staging", params={"status": ""})

    assert response.status_code == 200
    assert calls == [(None, 50)]


async def test_staging_mysql_failure_returns_502(client, monkeypatch):
    monkeypatch.setattr(kb_api.store, "staging_counts", _db_down)

    response = await client.get("/api/kb/staging")

    assert response.status_code == 502
    assert "MySQL" in response.json()["detail"]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_search_threshold_and_order(client, monkeypatch):
    calls: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        kb_api,
        "search_hits",
        lambda query, top_k=None: (
            calls.append((query, top_k))
            or [_hit(2, 0.9), _hit(1, 0.4), _hit(3, 0.7)]
        ),
    )
    _patch_settings(monkeypatch, knowledge_min_score=0.5)

    response = await client.post(
        "/api/kb/search",
        json={"query": "邮费是多少", "top_k": 3, "route": "dense"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["threshold"] == 0.5
    assert [hit["chunk_id"] for hit in body["hits"]] == [2, 3]
    assert [hit["score"] for hit in body["hits"]] == [0.9, 0.7]
    assert body["message"] is None
    assert calls == [("邮费是多少", 3)]


async def test_search_no_hits_returns_wu_pipei_message(client, monkeypatch):
    monkeypatch.setattr(kb_api, "search_hits", lambda query, top_k=None: [])
    _patch_settings(monkeypatch)

    response = await client.post("/api/kb/search", json={"query": "不存在"})

    assert response.status_code == 200
    body = response.json()
    assert body["hits"] == []
    assert body["message"] == "无匹配"


async def test_search_all_below_threshold_returns_no_match(client, monkeypatch):
    monkeypatch.setattr(
        kb_api,
        "search_hits",
        lambda query, top_k=None: [_hit(1, 0.31), _hit(2, 0.12)],
    )
    _patch_settings(monkeypatch, knowledge_min_score=0.5)

    response = await client.post("/api/kb/search", json={"query": "无关"})

    assert response.status_code == 200
    assert response.json()["hits"] == []
    assert response.json()["message"] == "无匹配"


async def test_search_unknown_route_returns_422(client):
    response = await client.post(
        "/api/kb/search", json={"query": "邮费", "route": "hybrid"}
    )

    assert response.status_code == 422


async def test_search_backend_failure_returns_502(client, monkeypatch):
    monkeypatch.setattr(
        kb_api,
        "search_hits",
        lambda query, top_k=None: (_ for _ in ()).throw(RuntimeError("milvus down")),
    )
    _patch_settings(monkeypatch)

    response = await client.post("/api/kb/search", json={"query": "邮费"})

    assert response.status_code == 502
    assert "milvus down" in response.json()["detail"]
