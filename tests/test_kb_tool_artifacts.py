import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

import app.executor as executor
import app.tools as tools
from app.config import get_settings
from app.kb.contracts import Hit, QueryPlan, RetrievalResult


def result(raw='运费', index=1):
    plan = QueryPlan(raw_query=raw, normalized_query=raw, lexical_query=raw)
    hit = Hit(index, .9, '物流', '运费多少', '运费10元', '文档章节', '配送 > 运费')
    return RetrievalResult(plan, 'hybrid_rerank', (hit,), (index,))


@pytest.fixture
def faq(monkeypatch):
    captured = []
    settings = get_settings().model_copy(update={'kb_corpus_version': 'ch04'})
    monkeypatch.setattr(tools, 'get_settings', lambda: settings, raising=False)
    monkeypatch.setattr(tools, 'retrieve', lambda *a: pytest.fail('ch04不可走旧检索'))
    def retrieve(query, **kwargs):
        captured.append((query, kwargs))
        return result(query)
    monkeypatch.setattr(tools, 'retrieve_result', retrieve, raising=False)
    return captured


def test_plain_invoke_still_returns_original_string(faq):
    text = tools.query_faq.invoke({'keyword': '运费'})
    assert text == '1. 运费多少\n→ 运费10元'
    assert len(faq) == 1


def test_toolcall_contains_retrieval_artifact(faq):
    response = tools.query_faq.invoke({'type': 'tool_call', 'name': 'query_faq', 'id': 'call-1',
                                      'args': {'keyword': '运费'}})
    assert isinstance(response, ToolMessage)
    assert response.tool_call_id == 'call-1'
    assert isinstance(response.artifact, RetrievalResult)
    assert response.artifact.hits[0].chunk_id == 1


def test_plan_injection_hidden_and_context_wins(faq):
    assert set(tools.query_faq.tool_call_schema.model_fields) == {'keyword'}
    trusted = result('真实原话').plan
    forged = result('模型伪造').plan
    response = executor.execute_tool_result('query_faq', {'keyword': '模型缩写', 'query_plan': forged},
        context={'query_plan': trusted}, tool_call_id='call-real')
    assert response.status == 'success'
    assert faq == [('真实原话', {'plan': trusted})]
    assert isinstance(response.artifact, RetrievalResult)


def test_model_cannot_supply_hidden_plan_without_system_context(faq):
    executor.execute_tool_result('query_faq', {'keyword': '原话', 'query_plan': result('伪造').plan})
    assert faq == [('原话', {'plan': None})]


def test_legacy_execute_tool_returns_str(faq):
    assert executor.execute_tool('query_faq', {'keyword': '运费'}) == '1. 运费多少\n→ 运费10元'


def test_timeout_uses_only_second_artifact_and_late_first_never_writes_pool(monkeypatch):
    first_started, release, first_finished = threading.Event(), threading.Event(), threading.Event()
    pool_writes = []
    from app.kb import low_confidence
    monkeypatch.setattr(low_confidence, 'record_low_confidence', lambda *a, **kw: pool_writes.append((a, kw)))
    counter = []
    first, second = result('first', 1), result('second', 2)
    @tool(response_format='content_and_artifact')
    def slow_faq(keyword: str):
        """控制第一次超时晚返回，第二次及时成功。"""
        counter.append(1)
        if len(counter) == 1:
            first_started.set()
            release.wait(2)
            first_finished.set()
            return 'first', first
        return 'second', second
    monkeypatch.setattr(tools, 'TOOL_REGISTRY', {'query_faq': slow_faq})
    monkeypatch.setattr(executor, 'get_settings', lambda: SimpleNamespace(tool_timeout=.01, knowledge_tool_timeout=.04))
    try:
        response = executor.execute_tool_result('query_faq', {'keyword': 'q'}, tool_call_id='call-a')
        assert first_started.is_set()
        assert response.artifact is second and response.content == 'second' and response.status == 'success'
    finally:
        release.set()
        assert first_finished.wait(2)
    assert response.artifact is second and pool_writes == []


def test_concurrent_conversations_artifacts_never_cross(monkeypatch):
    barrier = threading.Barrier(2)
    @tool(response_format='content_and_artifact')
    def concurrent_faq(keyword: str):
        """并发检索假工具。"""
        barrier.wait(2)
        return keyword, result(keyword, int(keyword))
    monkeypatch.setattr(tools, 'TOOL_REGISTRY', {'query_faq': concurrent_faq})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(executor.execute_tool_result, 'query_faq', {'keyword': str(n)}) for n in (1, 2)]
        responses = [f.result(3) for f in futures]
    assert [r.artifact.hits[0].chunk_id for r in responses] == [1, 2]


def test_failure_has_no_artifact_and_uses_faq_budget(monkeypatch):
    @tool(response_format='content_and_artifact')
    def broken_faq(keyword: str):
        """假服务失败。"""
        raise RuntimeError('private token must not leak')
    monkeypatch.setattr(tools, 'TOOL_REGISTRY', {'query_faq': broken_faq})
    response = executor.execute_tool_result('query_faq', {'keyword': 'q'})
    assert response.status == 'error' and response.artifact is None
    assert 'private token' not in response.content


def test_system_plan_raw_question_wins_even_if_model_keyword_is_empty(faq):
    trusted = result('用户完整原话').plan
    response = executor.execute_tool_result('query_faq', {'keyword': ''}, context={'query_plan': trusted})
    assert response.status == 'success'
    assert faq == [('用户完整原话', {'plan': trusted})]
