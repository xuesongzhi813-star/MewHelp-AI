"""Official SQLite lifecycle and single-process per-session mutual exclusion."""
import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager, aclosing
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.messages import HumanMessage

from app.executor import execute_tool_result
from app.kb.retriever import retrieve_result
from app.kb.low_confidence import record_low_confidence
from app.kb.embedding import EmbeddingError
from app.kb.reranker import RerankError
from app.workflow.agent import new_budget
from app.workflow.graph import WorkflowDeps, build_workflow
from app.workflow.models import ModelGateway

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_checkpointer(path: str):
    if path == ':memory:':
        raise ValueError('工作流持久化必须使用文件路径')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        await saver.setup()
        yield saver


class SessionLocks:
    def __init__(self):
        self._entries = {}

    @property
    def size(self):
        return len(self._entries)

    @asynccontextmanager
    async def hold(self, session_id: str):
        # No await between lookup and increment: atomic on our application loop.
        entry = self._entries.setdefault(session_id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del self._entries[session_id]


class WorkflowRuntime:
    def __init__(self, checkpointer, store, *, gateway_factory=ModelGateway,
                 retrieve=retrieve_result, execute=execute_tool_result, record=record_low_confidence):
        self.checkpointer, self.store = checkpointer, store
        self.gateway_factory = gateway_factory
        self.retrieve, self.execute, self.record = retrieve, execute, record
        self.locks = SessionLocks()

    def config(self, session_id):
        return {'configurable': {'thread_id': session_id}, 'recursion_limit': 64}

    def graph(self, model=None):
        return build_workflow(checkpointer=self.checkpointer, deps=WorkflowDeps(
            self.gateway_factory(model), self.store, self.retrieve, self.execute, self.record))

    async def get_state(self, session_id):
        return (await self.graph().aget_state(self.config(session_id))).values

    async def save_actions(self, session_id, actions):
        await self.graph().aupdate_state(self.config(session_id), {'actions': actions}, as_node='log_result')

    async def _interrupt(self, session_id, request_id):
        state = await self.get_state(session_id)
        if state.get('request_id') != request_id:
            return
        actions = dict(state.get('actions', {}))
        for key in state.get('turn_action_ids', []):
            if key in actions and actions[key]['status'] == 'available':
                actions[key] = {**actions[key], 'status': 'interrupted'}
        await self.graph().aupdate_state(self.config(session_id), {'status': 'interrupted', 'actions': actions}, as_node='log_result')

    async def stream_turn(self, session_id: str, question: str, *, model):
        async with self.locks.hold(session_id):
            request_id = uuid.uuid4().hex
            completed = False
            try:
                old = await self.get_state(session_id)
                conversation_id = await asyncio.to_thread(self.store.get_or_create_conversation, session_id)
                history = []
                if not old:
                    history = await asyncio.to_thread(self.store.get_history, session_id)
                    for index, message in enumerate(history):
                        if not message.id:
                            message.id = f'import-{conversation_id}-{index}'
                human = HumanMessage(content=question, id=f'user-{request_id}')
                await asyncio.to_thread(self.store.append_message, session_id, human)
                old_ids = {message.id for message in old.get('messages', []) + history}
                # New input restarts at START; never resume a crashed tool task.
                budget = new_budget()
                data = {'messages': [*history, human], 'session_id': session_id, 'conversation_id': conversation_id,
                        'request_id': request_id, 'raw_question': question, 'resolved_question': question,
                        'intent': '', 'route': '', 'retrieval': None, 'gate_reason': '', 'budget': budget.model_dump(),
                        'status': 'running', 'pending_decision': None, 'control': None,
                        'actions': old.get('actions', {}), 'turn_action_ids': [], 'node_trace': [], 'final_text': ''}
                graph = self.graph(model)
                async with asyncio.timeout(max(0.001, budget.deadline - time.time())):
                    async with aclosing(graph.astream(data, self.config(session_id), stream_mode='custom')) as stream:
                        async for event in stream:
                            yield event
                state = await self.get_state(session_id)
                for message in state.get('messages', []):
                    if message.id not in old_ids and message.id != human.id:
                        await asyncio.to_thread(self.store.append_message, session_id, message)
                completed = True
                items = [{**state['actions'][key], 'label': {'handoff': '转人工', 'create_ticket': '建工单'}[state['actions'][key]['kind']]}
                         for key in state.get('turn_action_ids', [])]
                if items:
                    yield {'type': 'actions', 'items': items}
                yield {'type': 'done'}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('workflow interrupted request=%s exception=%s', request_id, type(exc).__name__)
                message = (exc.public_message if isinstance(exc, EmbeddingError) else
                           '重排服务暂时不可用，请稍后重试或联系人工客服。' if isinstance(exc, RerankError) else
                           '处理消息时出现错误，请稍后重试或联系人工客服。')
                yield {'type': 'error', 'message': message}
            finally:
                if not completed:
                    try:
                        await asyncio.shield(self._interrupt(session_id, request_id))
                    except Exception as exc:
                        logger.warning('workflow checkpoint interrupt failed exception=%s', type(exc).__name__)
