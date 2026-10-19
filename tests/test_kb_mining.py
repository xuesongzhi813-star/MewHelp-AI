"""对话挖掘 job 测试(Task 11):真实 MySQL 回滚 + Fake 抽取器/嵌入/Milvus。

与 ``test_kb_store.py`` / ``test_kb_dualwrite.py`` 同一套路:
- 把 ``app.kb.store.get_session_local`` 换成绑定外层事务连接的 sessionmaker;
- 用例开始前在同一事务里清空 knowledge_chunks 与 qa_extraction_staging,
  使全表判重/计数只看到本用例的数据,结束整体 rollback;
- LLM、embedding、Milvus、双写向量化全部用 fake,绝不访问外部服务。
"""

from __future__ import annotations

import logging
import re
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

import app.kb.dualwrite as dualwrite
import app.kb.mining as mining
import app.kb.store as store
from app.db import get_engine
from app.kb.embedding import EmbeddingError
from app.models import Conversation, Message, QaExtractionStaging
from app.kb.store import ConversationForMining


# ---------------------------------------------------------------------------
# Fixture 与 fake
# ---------------------------------------------------------------------------


@pytest.fixture
def kb_tx(monkeypatch):
    """注入回滚会话工厂,并清空 chunks/staging 保证全表读取只看到本用例。"""
    with get_engine().connect() as conn:
        outer = conn.begin()
        session_local = sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint"
        )
        monkeypatch.setattr(store, "get_session_local", lambda: session_local)
        with Session(conn) as session:
            with session.begin():
                session.execute(delete(QaExtractionStaging))
                session.execute(delete(store.KnowledgeChunk))
                # run_once 读取整张 conversations 表;先清消息再清会话,
                # 让每个用例只看到自己创建的数据。rollback 会恢复已提交行
                # (包括 seed-ch03-* 演示会话),不会污染真实库。
                session.execute(delete(Message))
                session.execute(delete(Conversation))
        try:
            yield conn
        finally:
            outer.rollback()


def _unique(scope: str) -> str:
    return f"test-kb-mining-{scope}-{uuid.uuid4().hex}"


class FakeExtractor:
    """按调用序号返回预置结果;可返回异常以模拟 LLM 失败。"""

    def __init__(self, results, harness: "Harness | None" = None) -> None:
        self.results = list(results) if isinstance(results, (list, tuple)) else [results]
        self.calls = 0
        self.prompts: list[object] = []
        self.harness = harness

    def invoke(self, messages):
        self.calls += 1
        self.prompts.append(messages)
        if self.harness is not None:
            self.harness.events.append("extract")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class EchoExtractor:
    """从 prompt 的 ``### 会话 {id}`` 标题生成问答对,用于批处理断言。"""

    def __init__(self, harness: "Harness | None" = None) -> None:
        self.calls = 0
        self.prompts: list[str] = []
        self.harness = harness

    def invoke(self, messages):
        self.calls += 1
        prompt = str(messages)
        self.prompts.append(prompt)
        if self.harness is not None:
            self.harness.events.append("extract")
        ids = [int(value) for value in re.findall(r"### 会话 (\d+)", prompt)]
        return mining.QAPairList(
            pairs=[
                mining.QAPair(
                    conversation_id=conversation_id,
                    question=f"退货问题{conversation_id}?",
                    answer="签收后 7 天内可申请退货,商品需保持完好。",
                )
                for conversation_id in ids
            ]
        )


class Harness:
    """记录挖掘过程中的外部调用。"""

    def __init__(self, search_results=(), *, fail_embed: bool = False) -> None:
        self.events: list[str] = []
        self.embed_calls: list[list[str]] = []
        self.search_calls: list[tuple[list[float], int]] = []
        self.vectorize_calls = 0
        self.search_results = list(search_results)
        self.fail_embed = fail_embed

    def vectorize_pending(self):
        self.vectorize_calls += 1
        self.events.append("vectorize")
        return dualwrite.VectorizeResult(processed=0, failed=0, pending_left=0)

    def embed(self, texts):
        self.embed_calls.append(list(texts))
        if self.fail_embed:
            raise EmbeddingError("embedding 402")
        return [[0.01] * 4 for _ in texts]

    def search(self, vector, limit):
        self.search_calls.append((list(vector), limit))
        return list(self.search_results)


def _install(
    monkeypatch,
    extractor,
    *,
    harness: Harness | None = None,
    search_results=(),
    batch_size: int = 5,
    max_chars: int = 6000,
    dedup_score: float = 0.92,
    fail_embed: bool = False,
):
    harness = harness or Harness(search_results)
    harness.search_results = list(search_results)
    harness.fail_embed = fail_embed
    settings = SimpleNamespace(
        mining_batch_size=batch_size,
        mining_max_chars=max_chars,
        knowledge_dedup_score=dedup_score,
    )
    monkeypatch.setattr(mining, "get_settings", lambda: settings)
    monkeypatch.setattr(mining, "build_mining_model", lambda _settings: extractor)
    monkeypatch.setattr(mining, "embed_texts", harness.embed)
    monkeypatch.setattr(
        mining,
        "milvus",
        SimpleNamespace(search=harness.search),
    )
    monkeypatch.setattr(dualwrite, "vectorize_pending", harness.vectorize_pending)
    return harness


def _seed_conversation(conn, turns, *, user_id: str | None = None):
    """插入一条会话及其消息,返回 (conversation_id, [message_id, ...])。"""
    user_id = user_id or _unique("conv")
    with Session(conn) as session:
        with session.begin():
            conversation = Conversation(user_id=user_id)
            session.add(conversation)
            session.flush()
            ids: list[int] = []
            for role, content in turns:
                message = Message(
                    conversation_id=conversation.id,
                    role=role,
                    content=content,
                )
                session.add(message)
                session.flush()
                ids.append(message.id)
            return conversation.id, ids


def _default_turns():
    return [
        ("user", "怎么退货?"),
        ("assistant", "签收后 7 天内,商品完好即可申请退货,退款 1-3 个工作日原路退回。"),
    ]


def _staging_rows():
    return store.list_staging(limit=1000)


def _chunks():
    return store.list_recent(limit=1000)


# ---------------------------------------------------------------------------
# build_mining_model / prompt
# ---------------------------------------------------------------------------


def test_build_mining_model_uses_function_calling(monkeypatch):
    recorded = {}

    class FakeChat:
        def with_structured_output(self, schema, method=None):
            recorded["schema"] = schema
            recorded["method"] = method
            return "structured-runnable"

    monkeypatch.setattr(mining, "build_chat_model", lambda settings: FakeChat())

    result = mining.build_mining_model(SimpleNamespace())

    assert result == "structured-runnable"
    assert recorded["schema"] is mining.QAPairList
    assert recorded["method"] == "function_calling"


def test_build_mining_prompt_formats_turns_and_truncates(monkeypatch):
    _install_settings(monkeypatch, max_chars=1200)
    conversation = ConversationForMining(
        id=42,
        user_id="u",
        max_message_id=2,
        turns=(
            (1, "user", "退" * 600),
            (2, "assistant", "答" * 600),
        ),
    )

    prompt = mining.build_mining_prompt([conversation])

    assert "### 会话 42" in prompt
    assert "用户:" in prompt and "客服:" in prompt
    assert "conversation_id" in prompt
    # 单条消息截断到 500 字(保留原文前 500 字,不再出现第 501 字)
    assert "退" * 500 in prompt
    assert "退" * 501 not in prompt
    assert len(prompt) <= 1200


def test_build_mining_prompt_obeys_total_limit(monkeypatch):
    _install_settings(monkeypatch, max_chars=260)
    conversations = [
        ConversationForMining(
            id=index,
            user_id=f"u{index}",
            max_message_id=2,
            turns=((1, "user", "问" * 100), (2, "assistant", "答" * 100)),
        )
        for index in (1, 2)
    ]

    prompt = mining.build_mining_prompt(conversations)

    assert len(prompt) == 260


def _install_settings(monkeypatch, *, max_chars=6000, batch_size=5, dedup_score=0.92):
    settings = SimpleNamespace(
        mining_batch_size=batch_size,
        mining_max_chars=max_chars,
        knowledge_dedup_score=dedup_score,
    )
    monkeypatch.setattr(mining, "get_settings", lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# run_once: 基础抽取与暂存
# ---------------------------------------------------------------------------


def test_empty_pairs_writes_nothing(kb_tx, monkeypatch):
    conversation_id, _ = _seed_conversation(kb_tx, _default_turns())
    extractor = FakeExtractor(mining.QAPairList(pairs=[]))
    _install(monkeypatch, extractor)

    result = mining.run_once()

    assert result == mining.MiningResult(
        conversations=1, extracted=0, kept=0, discarded=0
    )
    assert store.staging_counts()["extracted"] == 0
    assert store.staging_counts()["kept"] == 0
    assert store.staging_counts()["discarded"] == 0
    assert extractor.calls == 1
    assert _chunks() == []


def test_extracted_pairs_go_to_staging_and_kept_chunk(kb_tx, monkeypatch):
    conversation_id, message_ids = _seed_conversation(kb_tx, _default_turns())
    pair = mining.QAPair(
        conversation_id=conversation_id,
        question="怎么退货?",
        answer="签收后 7 天内,商品完好即可申请退货。",
    )
    extractor = FakeExtractor(mining.QAPairList(pairs=[pair]))
    harness = _install(monkeypatch, extractor)

    result = mining.run_once()

    assert result == mining.MiningResult(
        conversations=1, extracted=1, kept=1, discarded=0
    )
    rows = _staging_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.question == pair.question
    assert row.answer == pair.answer
    assert row.status == "kept"
    assert re.fullmatch(r"M\d{14}-[A-Za-z0-9]{8}", row.batch_no)
    expected_ref = (
        f"conv:{conversation_id}:msgs:{message_ids[0]}-{message_ids[-1]}"
    )
    assert row.source_ref == expected_ref

    chunks = _chunks()
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.questions == pair.question
    assert chunk.category == "售后政策"
    assert chunk.section_path == f"对话挖掘 > 会话 {conversation_id}"
    assert chunk.content_type == "对话挖掘"
    assert chunk.is_key_clause is False

    # 判重先嵌入问题再查 Milvus;keep 后触发一次向量化,加上开跑前的一次
    assert harness.embed_calls == [[pair.question]]
    assert harness.search_calls == [([0.01] * 4, 1)]
    assert harness.vectorize_calls == 2
    assert harness.events[0] == "vectorize"
    assert harness.events[-1] == "vectorize"


def test_unknown_conversation_id_is_skipped_with_warning(
    kb_tx, monkeypatch, caplog
):
    conversation_id, _ = _seed_conversation(kb_tx, _default_turns())
    extractor = FakeExtractor(
        mining.QAPairList(
            pairs=[
                mining.QAPair(
                    conversation_id=conversation_id + 999_999,
                    question="未知问题?",
                    answer="未知答案",
                )
            ]
        )
    )
    _install(monkeypatch, extractor)

    with caplog.at_level(logging.WARNING, logger="app.kb.mining"):
        result = mining.run_once()

    assert result.extracted == 0
    assert _staging_rows() == []
    assert "conversation_id" in caplog.text


def test_skips_already_mined_conversation(kb_tx, monkeypatch):
    conversation_id, message_ids = _seed_conversation(kb_tx, _default_turns())
    store.insert_staging(
        [
            store.StagingCandidate(
                batch_no="M20260101000000-deadbeef",
                source_ref=(
                    f"conv:{conversation_id}:msgs:{message_ids[0]}-{message_ids[-1]}"
                ),
                question="历史问",
                answer="历史答",
            )
        ]
    )
    extractor = FakeExtractor(mining.QAPairList(pairs=[]))
    _install(monkeypatch, extractor)

    result = mining.run_once()

    assert result.conversations == 0
    assert extractor.calls == 0


def test_processes_conversation_with_new_messages_after_tail(kb_tx, monkeypatch):
    conversation_id, message_ids = _seed_conversation(kb_tx, _default_turns())
    # 上次只挖到第一条消息,后面还有未挖掘内容
    store.insert_staging(
        [
            store.StagingCandidate(
                batch_no="M20260101000000-deadbeef",
                source_ref=(
                    f"conv:{conversation_id}:msgs:{message_ids[0]}-{message_ids[0]}"
                ),
                question="历史问",
                answer="历史答",
            )
        ]
    )
    extractor = EchoExtractor()
    _install(monkeypatch, extractor)

    result = mining.run_once()

    assert result.conversations == 1
    assert extractor.calls == 1


def test_llm_failure_writes_no_staging_and_propagates(kb_tx, monkeypatch):
    _seed_conversation(kb_tx, _default_turns())
    extractor = FakeExtractor(RuntimeError("LLM 打盹了"))
    _install(monkeypatch, extractor)

    with pytest.raises(RuntimeError, match="LLM 打盹了"):
        mining.run_once()

    counts = store.staging_counts()
    assert counts["extracted"] == 0
    assert counts["kept"] == 0
    assert counts["discarded"] == 0
    assert _chunks() == []


def test_later_batch_llm_failure_keeps_previous_batch_and_rerun(
    kb_tx, monkeypatch
):
    """第 1 批成功、第 2 批 LLM 失败:第 2 批不落库,补跑只处理第 2 批。"""
    first_id, first_ids = _seed_conversation(kb_tx, _default_turns())
    second_id, second_ids = _seed_conversation(kb_tx, _default_turns())
    first_pair = mining.QAPair(
        conversation_id=first_id,
        question="第一批问题?",
        answer="第一批答案。",
    )
    second_pair = mining.QAPair(
        conversation_id=second_id,
        question="第二批问题?",
        answer="第二批答案。",
    )
    extractor = FakeExtractor(
        [
            mining.QAPairList(pairs=[first_pair]),
            RuntimeError("第二批 LLM 失败"),
        ]
    )
    _install(monkeypatch, extractor, batch_size=1)

    with pytest.raises(RuntimeError, match="第二批 LLM 失败"):
        mining.run_once()

    assert extractor.calls == 2
    rows = _staging_rows()
    assert len(rows) == 1
    assert rows[0].status == "kept"
    assert rows[0].source_ref.startswith(f"conv:{first_id}:msgs:")
    assert store.staging_tail(first_id) == first_ids[-1]
    # 第 2 批没有留下任何暂存行,staging_tail 不前进
    assert store.staging_tail(second_id) is None
    assert len(_chunks()) == 1

    rerun = FakeExtractor(mining.QAPairList(pairs=[second_pair]))
    monkeypatch.setattr(mining, "build_mining_model", lambda _settings: rerun)

    result = mining.run_once()

    assert rerun.calls == 1
    assert result.conversations == 1
    assert result.extracted == 1 and result.kept == 1
    assert store.staging_tail(second_id) == second_ids[-1]
    assert len(_chunks()) == 2


def test_dedup_failure_rolls_back_staging_and_rerun_processes(
    kb_tx, monkeypatch
):
    """embedding 失败时删除本批暂存行,staging_tail 不前进,补跑可重抽。"""
    conversation_id, message_ids = _seed_conversation(kb_tx, _default_turns())
    pair = mining.QAPair(
        conversation_id=conversation_id,
        question="怎么退货?",
        answer="签收后 7 天内可申请退货。",
    )
    extractor = FakeExtractor(mining.QAPairList(pairs=[pair]))
    harness = _install(monkeypatch, extractor, fail_embed=True)

    with pytest.raises(EmbeddingError, match="embedding 402"):
        mining.run_once()

    # 暂存行已整批删除,source_ref 没有推进 tail
    assert _staging_rows() == []
    assert store.staging_tail(conversation_id) is None
    assert _chunks() == []

    harness.fail_embed = False
    rerun = FakeExtractor(mining.QAPairList(pairs=[pair]))
    monkeypatch.setattr(mining, "build_mining_model", lambda _settings: rerun)

    result = mining.run_once()

    assert rerun.calls == 1
    assert result.conversations == 1
    assert result.extracted == 1 and result.kept == 1
    assert store.staging_tail(conversation_id) == message_ids[-1]
    assert len(_chunks()) == 1


# ---------------------------------------------------------------------------
# run_once: 精确 / 向量判重
# ---------------------------------------------------------------------------


def test_exact_duplicate_marked_discarded(kb_tx, monkeypatch):
    conversation_id, _ = _seed_conversation(kb_tx, _default_turns())
    store.insert_chunks(
        [
            store.ChunkCandidate(
                category="售后政策",
                questions="怎么退货?",
                answer="已有答案",
                section_path="已有 > 路径",
                content_type="文档章节",
                is_key_clause=False,
            )
        ]
    )
    extractor = FakeExtractor(
        mining.QAPairList(
            pairs=[
                mining.QAPair(
                    conversation_id=conversation_id,
                    question="怎么 退货？",  # 归一化后与库内问题相同
                    answer="签收后 7 天内可退货。",
                )
            ]
        )
    )
    harness = _install(monkeypatch, extractor)

    result = mining.run_once()

    assert result.kept == 0
    assert result.discarded == 1
    rows = _staging_rows()
    assert len(rows) == 1 and rows[0].status == "discarded"
    assert harness.embed_calls == []  # 精确命中后不再向量检索
    assert len(_chunks()) == 1  # 只有预置的那一条


def test_vector_duplicate_marked_discarded(kb_tx, monkeypatch):
    conversation_id, _ = _seed_conversation(kb_tx, _default_turns())
    extractor = FakeExtractor(
        mining.QAPairList(
            pairs=[
                mining.QAPair(
                    conversation_id=conversation_id,
                    question="全新问题?",
                    answer="全新答案",
                )
            ]
        )
    )
    harness = _install(
        monkeypatch, extractor, search_results=[("123", 0.99)]
    )

    result = mining.run_once()

    assert result.kept == 0
    assert result.discarded == 1
    rows = _staging_rows()
    assert len(rows) == 1 and rows[0].status == "discarded"
    assert harness.embed_calls == [["全新问题?"]]
    assert harness.search_calls == [([0.01] * 4, 1)]
    assert _chunks() == []


def test_vector_score_below_threshold_is_kept(kb_tx, monkeypatch):
    conversation_id, _ = _seed_conversation(kb_tx, _default_turns())
    extractor = FakeExtractor(
        mining.QAPairList(
            pairs=[
                mining.QAPair(
                    conversation_id=conversation_id,
                    question="全新问题?",
                    answer="全新答案",
                )
            ]
        )
    )
    _install(monkeypatch, extractor, search_results=[("123", 0.5)])

    result = mining.run_once()

    assert (result.kept, result.discarded) == (1, 0)
    assert len(_chunks()) == 1


def test_duplicate_questions_within_one_run_only_keep_first(kb_tx, monkeypatch):
    first_id, _ = _seed_conversation(kb_tx, _default_turns())
    second_id, _ = _seed_conversation(kb_tx, _default_turns())
    extractor = FakeExtractor(
        mining.QAPairList(
            pairs=[
                mining.QAPair(
                    conversation_id=first_id,
                    question="同样的问题?",
                    answer="答案一",
                ),
                mining.QAPair(
                    conversation_id=second_id,
                    question="同样的问题？",  # 归一化后相同
                    answer="答案二",
                ),
            ]
        )
    )
    harness = _install(monkeypatch, extractor)

    result = mining.run_once()

    assert (result.kept, result.discarded) == (1, 1)
    assert len(_chunks()) == 1
    assert len(harness.embed_calls) == 1


# ---------------------------------------------------------------------------
# run_once: 批次与 limit
# ---------------------------------------------------------------------------


def test_batches_by_mining_batch_size(kb_tx, monkeypatch):
    conversation_ids = [
        _seed_conversation(kb_tx, _default_turns())[0] for _ in range(3)
    ]
    extractor = EchoExtractor()
    _install(monkeypatch, extractor, batch_size=2)

    result = mining.run_once()

    assert result.conversations == 3
    assert result.extracted == 3
    assert extractor.calls == 2
    # 首批 2 个会话,第二批 1 个(Prompt 说明里的「{id}」不是真实会话)
    assert len(re.findall(r"### 会话 \d+", extractor.prompts[0])) == 2
    assert len(re.findall(r"### 会话 \d+", extractor.prompts[1])) == 1
    assert {
        int(value) for value in re.findall(r"### 会话 (\d+)", "".join(extractor.prompts))
    } == set(conversation_ids)


def test_batches_by_mining_max_chars(kb_tx, monkeypatch):
    turns = [
        ("user", "退" * 90),
        ("assistant", "答" * 90),
    ]
    for _ in range(3):
        _seed_conversation(kb_tx, turns)
    extractor = EchoExtractor()
    _install(monkeypatch, extractor, batch_size=10, max_chars=600)

    result = mining.run_once()

    assert result.conversations == 3
    # 每个会话块约 200 字,600 字上限下每批只装得下一个
    assert extractor.calls == 3
    assert all(
        len(prompt) <= 600 for prompt in extractor.prompts
    )


def test_limit_caps_conversations(kb_tx, monkeypatch):
    conversation_ids = [
        _seed_conversation(kb_tx, _default_turns())[0] for _ in range(3)
    ]
    extractor = EchoExtractor()
    _install(monkeypatch, extractor)

    result = mining.run_once(limit=2)

    assert result.conversations == 2
    assert result.extracted == 2
    prompt = extractor.prompts[0]
    assert set(int(value) for value in re.findall(r"### 会话 (\d+)", prompt)) == set(
        conversation_ids[:2]
    )


def test_no_conversations_returns_zero(kb_tx, monkeypatch):
    extractor = FakeExtractor(mining.QAPairList(pairs=[]))
    harness = _install(monkeypatch, extractor)

    result = mining.run_once()

    assert result == mining.MiningResult(
        conversations=0, extracted=0, kept=0, discarded=0
    )
    assert extractor.calls == 0
    # 开跑前仍会先补一次 pending 向量化
    assert harness.vectorize_calls == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_once_prints_mining_result(kb_tx, monkeypatch, capsys):
    calls = []

    def fake_run_once(limit=None):
        calls.append(limit)
        return mining.MiningResult(
            conversations=2, extracted=3, kept=1, discarded=2
        )

    monkeypatch.setattr(mining, "run_once", fake_run_once)

    assert mining.main(["--once"]) == 0

    assert calls == [None]
    out = capsys.readouterr().out
    assert "2" in out and "3" in out and "1" in out and "2" in out


def test_main_passes_limit(kb_tx, monkeypatch, capsys):
    calls = []

    def fake_run_once(limit=None):
        calls.append(limit)
        return mining.MiningResult(
            conversations=0, extracted=0, kept=0, discarded=0
        )

    monkeypatch.setattr(mining, "run_once", fake_run_once)

    assert mining.main(["--limit", "7"]) == 0

    assert calls == [7]


def test_main_interval_loops_and_reports_errors(kb_tx, monkeypatch, capsys):
    calls = []
    sleeps = []

    def fake_run_once(limit=None):
        calls.append(limit)
        if len(calls) == 2:
            raise RuntimeError("第二轮失败")
        return mining.MiningResult(
            conversations=1, extracted=1, kept=1, discarded=0
        )

    monkeypatch.setattr(mining, "run_once", fake_run_once)
    monkeypatch.setattr(mining.time, "sleep", lambda seconds: sleeps.append(seconds))

    assert mining.main(["--interval", "5"]) == 1

    assert calls == [None, None]
    assert sleeps == [5]
    assert "第二轮失败" in capsys.readouterr().err
