"""Exercise real SDK exports without sending telemetry to a cloud project."""
import asyncio
from contextlib import aclosing
from dataclasses import asdict
from types import SimpleNamespace
import uuid

import pytest


@pytest.fixture
def observed(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from app.observability.runtime import ObservabilityRuntime
    sink = InMemorySpanExporter()
    client = Langfuse(public_key='pk-test-' + uuid.uuid4().hex, secret_key='sk-test',
                      base_url='https://jp.cloud.langfuse.com', span_exporter=sink)
    monkeypatch.setattr(client, 'auth_check', lambda: True)
    settings = SimpleNamespace(langfuse_enabled=True, langfuse_public_key=client._resources.public_key,
        langfuse_secret_key='sk-test', langfuse_base_url='https://jp.cloud.langfuse.com',
        langfuse_timeout=1, langfuse_shutdown_timeout=5)
    runtime = ObservabilityRuntime(settings, client_factory=lambda **kw: client)
    assert runtime.start() == 'ready'
    yield runtime, sink
    runtime.close()


@pytest.mark.asyncio
async def test_concurrent_streams_and_thread_spans_keep_their_own_trace(observed):
    from app.observability.spans import traced_stream, trace_span, bind_turn, tracing_config, current_request_id
    from app.flywheel.contracts import TurnIdentity
    runtime, sink = observed

    class Chat:
        observability = runtime

        @traced_stream('chat')
        async def stream(self, session_id):
            identity = TurnIdentity(session_id, 1, 10, current_request_id(), current_request_id())
            bind_turn(identity)
            def query():
                with trace_span('mysql-read', input={'id': 10}) as span:
                    span.finish(output={'count': 1})
            await asyncio.to_thread(query)
            await asyncio.sleep(0)
            assert tracing_config()['metadata']['langfuse_session_id'] == session_id
            yield {'type': 'turn', **asdict(identity)}

    async def collect(session):
        return [event async for event in Chat().stream(session)]
    left, right = await asyncio.gather(collect('left'), collect('right'))
    runtime.close()
    roots = [s for s in sink.get_finished_spans() if s.name == 'chat']
    reads = [s for s in sink.get_finished_spans() if s.name == 'mysql-read']
    assert len(roots) == len(reads) == 2
    assert left[0]['trace_id'] != right[0]['trace_id']
    for child in reads:
        parent = next(s for s in roots if s.context.trace_id == child.context.trace_id)
        assert child.parent.span_id == parent.context.span_id


async def test_resolved_session_propagates_to_custom_and_llm_children(observed):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from app.observability.spans import execution_scope,bind_turn,trace_span,tracing_config,current_request_id
    from app.flywheel.contracts import TurnIdentity
    runtime,sink=observed
    with execution_scope('session-root',runtime):
        bind_turn(TurnIdentity('resolved-system-session',1,10,current_request_id(),current_request_id()))
        with trace_span('custom-child'):
            await asyncio.to_thread(lambda:FakeListChatModel(responses=['ok']).invoke('synthetic',config=tracing_config()))
    with execution_scope('unrelated',runtime):
        with trace_span('unrelated-child'):pass
    runtime.close()
    spans=sink.get_finished_spans()
    relevant=[s for s in spans if s.name in {'session-root','custom-child','FakeListChatModel'}]
    assert len(relevant)==3
    assert {s.name:s.attributes.get('session.id') for s in relevant}=={
        'session-root':'resolved-system-session','custom-child':'resolved-system-session','FakeListChatModel':'resolved-system-session'}
    assert not next(s for s in spans if s.name=='unrelated-child').attributes.get('session.id')


async def test_graph_state_observations_omit_history_trimmed_from_actual_prompt(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import HumanMessage
    from langgraph.graph import StateGraph,START,END
    from app.observability.runtime import ObservabilityRuntime
    from app.observability.spans import execution_scope,tracing_config
    sink=InMemorySpanExporter()
    def factory(**kwargs):
        client=Langfuse(**kwargs,span_exporter=sink)
        monkeypatch.setattr(client,'auth_check',lambda:True)
        return client
    settings=SimpleNamespace(langfuse_enabled=True,langfuse_public_key='pk-projection-'+uuid.uuid4().hex,
        langfuse_secret_key='sk-synthetic',langfuse_base_url='https://jp.cloud.langfuse.com',
        langfuse_timeout=1,langfuse_shutdown_timeout=5)
    runtime=ObservabilityRuntime(settings,client_factory=factory);assert runtime.start()=='ready'
    model=FakeListChatModel(responses=['CURRENT_ONLY'])
    async def node(state):
        # Represents the bounded model context; the persisted graph still retains old history.
        await model.ainvoke('CURRENT_ONLY',config=tracing_config())
        return {'final_text':'CURRENT_ONLY'}
    graph=StateGraph(dict).add_node('current_node',node).add_edge(START,'current_node').add_edge('current_node',END).compile()
    with execution_scope('projection-root',runtime):
        await graph.ainvoke({'raw_question':'CURRENT_QUERY','session_id':'synthetic-session',
            'messages':[HumanMessage(content='OLD_HISTORY_SENTINEL'),HumanMessage(content='CURRENT_ONLY')],
            'history_context':{'text':'OLD_HISTORY_SENTINEL'},'message_refs':{}},config=tracing_config())
    runtime.close()
    spans=sink.get_finished_spans()
    assert 'OLD_HISTORY_SENTINEL' not in '\n'.join(str(s.attributes) for s in spans)
    assert any('CURRENT_QUERY' in str(s.attributes) for s in spans if s.name=='current_node')
    assert any('CURRENT_ONLY' in str(s.attributes.get('langfuse.observation.input')) for s in spans
        if s.attributes.get('langfuse.observation.type')=='generation')


@pytest.mark.asyncio
async def test_sync_async_and_streaming_model_calls_export_exactly_once(observed):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from app.observability.spans import traced_stream, tracing_config
    runtime, sink = observed

    class Chat:
        observability = runtime

        @traced_stream('chat')
        async def stream(self):
            model = FakeListChatModel(responses=['sync', 'async', 'stream'])
            assert model.invoke('first', config=tracing_config()).content == 'sync'
            assert (await model.ainvoke('second', config=tracing_config())).content == 'async'
            content = ''.join([x.content async for x in model.astream('third', config=tracing_config())])
            yield {'type': 'token', 'content': content}

    result = [event async for event in Chat().stream()]
    runtime.close()
    generations = [s for s in sink.get_finished_spans()
                   if s.attributes.get('langfuse.observation.type') == 'generation']
    assert result == [{'type': 'token', 'content': 'stream'}]
    assert len(generations) == 3
    assert len({s.context.trace_id for s in generations}) == 1


async def test_background_gateway_generations_are_exported_once_inside_and_outside_graph(observed):
    import json
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.runnables import RunnableLambda
    from langgraph.graph import StateGraph,START,END
    from app.workflow.models import ModelGateway
    from app.workflow.contracts import BudgetState
    from app.observability.spans import execution_scope,tracing_config
    runtime,sink=observed
    class Provider:
        def with_structured_output(self,schema,**kwargs):
            model=FakeListChatModel(responses=['{"can_answer":true,"reason":"有证据"}'])
            return model | RunnableLambda(lambda raw:{'raw':raw,'parsed':json.loads(raw.content),'parsing_error':None})
    async def invoke(state):
        await ModelGateway(Provider()).check_sufficiency('合成问题',BudgetState())
        return {}
    graph=StateGraph(dict).add_node('check',invoke).add_edge(START,'check').add_edge('check',END).compile()
    with execution_scope('outside',runtime,purpose='flywheel'):
        await invoke({})
    with execution_scope('inside',runtime,purpose='evaluation'):
        await graph.ainvoke({},config=tracing_config())
    runtime.close()
    generations=[s for s in sink.get_finished_spans() if s.attributes.get('langfuse.observation.type')=='generation']
    assert len(generations)==2
    roots=[s for s in sink.get_finished_spans() if s.name in {'outside','inside'}]
    assert {s.context.trace_id for s in generations}=={s.context.trace_id for s in roots}


@pytest.mark.asyncio
async def test_closing_stream_closes_root_and_resets_context(observed):
    from app.observability.spans import traced_stream, current_request_id
    runtime, sink = observed

    class Chat:
        observability = runtime

        @traced_stream('cancelled-chat')
        async def stream(self):
            yield {'type': 'token', 'content': 'partial'}
            await asyncio.Event().wait()

    async with aclosing(Chat().stream()) as stream:
        assert (await anext(stream))['content'] == 'partial'
    assert current_request_id() is None
    runtime.close()
    assert len([s for s in sink.get_finished_spans() if s.name == 'cancelled-chat']) == 1


@pytest.mark.asyncio
async def test_workflow_persists_and_emits_exact_turn_identity(observed, tmp_path):
    from app.history import InMemorySessionStore
    from app.workflow.runtime import WorkflowRuntime, open_checkpointer
    from tests.test_workflow_graph import Gateway
    observed_runtime, sink = observed
    gateway = Gateway(intent='闲聊')
    async with open_checkpointer(str(tmp_path / 'traced.sqlite')) as saver:
        runtime = WorkflowRuntime(saver, InMemorySessionStore(10), gateway_factory=lambda _: gateway,
                                  observability=observed_runtime)
        events = [e async for e in runtime.stream_turn('s', '你好', model=gateway)]
        state = await runtime.get_state('s')
    turns = [e for e in events if e['type'] == 'turn']
    assert turns
    assert turns[-1]['user_message_id'] == state['current_turn_from_msg_id']
    assert turns[-1]['assistant_message_id'] is not None
    assert turns[-1]['request_id'] == state['request_id'] == turns[-1]['trace_id']
    assert state['turn_identity']['assistant_message_id'] == turns[-1]['assistant_message_id']
    observed_runtime.close()
    spans = sink.get_finished_spans()
    root = next(s for s in spans if s.name == 'chat')
    assert root.attributes['session.id'] == state['session_id']
    assert any(s.name == 'classify_intent' and s.context.trace_id == root.context.trace_id for s in spans)


@pytest.mark.asyncio
async def test_workflow_disabled_telemetry_still_emits_stable_turn(tmp_path):
    from app.history import InMemorySessionStore
    from app.workflow.runtime import WorkflowRuntime, open_checkpointer
    from tests.test_workflow_graph import Gateway
    gateway = Gateway(intent='闲聊')
    async with open_checkpointer(str(tmp_path / 'disabled.sqlite')) as saver:
        runtime = WorkflowRuntime(saver, InMemorySessionStore(10), gateway_factory=lambda _: gateway)
        events = [e async for e in runtime.stream_turn('s', '你好', model=gateway)]
        state = await runtime.get_state('s')
    turn = next(e for e in events if e['type'] == 'turn')
    assert len(turn['trace_id']) == 32
    assert turn['trace_id'] == state['turn_identity']['trace_id']


@pytest.mark.asyncio
async def test_ticket_resume_creates_new_trace_but_keeps_turn(observed, tmp_path):
    from app.workflow.runtime import open_checkpointer
    from app.schemas import ToolConfirmationRequest
    from tests.test_ticket_interrupt import setup_runtime
    observed_runtime, sink = observed
    async with open_checkpointer(str(tmp_path / 'ticket-trace.sqlite')) as saver:
        runtime, service, audit, calls, gateway = await setup_runtime(tmp_path, saver)
        runtime.observability = observed_runtime
        try:
            original = [e async for e in runtime.stream_turn('s', '包裹未收到，帮我建工单', model=gateway)]
            state = await runtime.get_state('s')
            request = ToolConfirmationRequest(session_id='s', preview_id=state['ticket_confirmation']['preview_id'],
                                              decision='cancel')
            resumed = [e async for e in runtime.stream_tool_confirmation(request, model=gateway)]
            final = await runtime.get_state('s')
        finally:
            await service.aclose()
    first = next(e for e in original if e['type'] == 'turn')
    last = [e for e in resumed if e['type'] == 'turn'][-1]
    assert last['trace_id'] != first['trace_id']
    assert last['user_message_id'] == first['user_message_id']
    assert final['continuation_of'] == first['request_id']
    assert not calls


@pytest.mark.asyncio
async def test_custom_retrieval_and_provider_functions_export_results(observed):
    from app.observability.spans import traced_stream
    from app.kb.retriever import retrieve_result
    from app.kb.embedding import embed_texts
    from app.kb.reranker import rerank
    from app.kb.store import fetch_by_ids
    runtime, sink = observed

    class Chat:
        observability = runtime

        @traced_stream('custom-steps')
        async def stream(self):
            assert retrieve_result('').hits == ()
            assert embed_texts([]) == []
            assert rerank('', []) == []
            assert fetch_by_ids([]) == {}
            yield {'type': 'done'}

    _ = [e async for e in Chat().stream()]
    runtime.close()
    names = {span.name for span in sink.get_finished_spans()}
    assert {'knowledge-retrieval', 'embedding', 'rerank', 'mysql-knowledge-read'} <= names


@pytest.mark.asyncio
async def test_legacy_chat_has_one_root_and_one_generation(observed):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from app.chat_service import stream_chat
    from app.history import InMemorySessionStore
    runtime, sink = observed

    class Model(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    frames = [e async for e in stream_chat('legacy-session', '你好', store=InMemorySessionStore(10),
        model=Model(responses=['你好']), observability=runtime)]
    assert any('done' in frame for frame in frames)
    runtime.close()
    spans = sink.get_finished_spans()
    roots = [s for s in spans if s.name == 'legacy-chat']
    generations = [s for s in spans if s.attributes.get('langfuse.observation.type') == 'generation']
    assert len(roots) == len(generations) == 1
    assert roots[0].context.trace_id == generations[0].context.trace_id


@pytest.mark.asyncio
async def test_extract_failure_is_traced_without_fabricated_session(observed):
    import httpx
    from langchain_core.runnables import RunnableLambda
    from app.main import create_app
    from app.dependencies import get_extractor
    runtime, sink = observed
    app = create_app()
    app.state.observability = runtime
    def fail(_):
        raise ValueError('synthetic-extract-failure')
    app.dependency_overrides[get_extractor] = lambda: RunnableLambda(fail)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        with pytest.raises(ValueError, match='synthetic-extract-failure'):
            await client.post('/extract', json={'message': '用户的售后申请'})
    runtime.close()
    root = [s for s in sink.get_finished_spans() if s.name == 'extract']
    assert len(root) == 1
    assert 'session.id' not in root[0].attributes


@pytest.mark.asyncio
async def test_query_executor_thread_carries_request_context(observed):
    from app.observability.spans import traced_stream, current_request_id
    from app.kb.query import build_query_plan
    runtime, sink = observed
    seen = []

    class Query:
        def invoke(self, messages):
            seen.append(current_request_id())
            return {'normalized_query':'运费是多少', 'intent':'knowledge'}

    class Chat:
        observability = runtime

        @traced_stream('rewrite')
        async def stream(self):
            request = current_request_id()
            await asyncio.to_thread(build_query_plan, '运费是多少', model=Query())
            assert seen == [request]
            yield {'type':'done'}

    _ = [e async for e in Chat().stream()]


@pytest.mark.asyncio
async def test_background_operation_starts_its_own_trace(observed):
    from app.observability.spans import traced_stream, traced_operation, current_request_id
    runtime, sink = observed
    release = asyncio.Event()
    ids = {}

    class Owner:
        observability = runtime

        @traced_operation('summary', purpose='summary')
        async def summarize(self):
            ids['summary'] = current_request_id()

        @traced_stream('source-chat')
        async def stream(self):
            ids['chat'] = current_request_id()
            async def background():
                await release.wait()
                await self.summarize()
            self.task = asyncio.create_task(background())
            yield {'type':'done'}

    owner = Owner()
    _ = [e async for e in owner.stream()]
    release.set()
    await owner.task
    runtime.close()
    assert ids['summary'] != ids['chat']
    spans = sink.get_finished_spans()
    assert len({s.context.trace_id for s in spans if s.name in {'source-chat','summary'}}) == 2
