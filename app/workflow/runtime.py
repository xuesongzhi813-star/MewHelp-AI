"""Official SQLite lifecycle and single-process per-session mutual exclusion."""
import asyncio
import logging
import time
import uuid
from dataclasses import asdict, replace
from contextlib import asynccontextmanager, aclosing
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from app.executor import execute_tool_result
from app.kb.retriever import retrieve_result
from app.kb.low_confidence import record_low_confidence
from app.kb.embedding import EmbeddingError
from app.kb.reranker import RerankError
from app.workflow.agent import new_budget
from app.workflow.graph import WorkflowDeps, build_workflow
from app.workflow.models import ModelGateway
from app.config import get_settings
from app.context.contracts import ConversationIdentity
from app.context.identity import parse_canonical
from app.context.store import ConversationStore, InMemoryConversationStore
from app.context.layers import build_layered_view, summary_batch, summary_text
from app.context.tokens import count_model_tokens, estimate_text
from app.context.budget import derive_budget, ContextBudgetError
from app.flywheel.contracts import TurnIdentity
from app.observability.spans import traced_stream, tracing_config, current_request_id, bind_turn, update_trace
from app.flywheel.publishing import knowledge_turn_stream

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
    def __init__(self, checkpointer, store, *, gateway_factory=ModelGateway, context_store=None, summary_manager=None,
                 retrieve=retrieve_result, execute=execute_tool_result, record=record_low_confidence,tool_service=None,
                 observability=None, evidence_policy=None, self_check_enabled=False,release_coordinator=None,
                 read_only=False,trace_purpose='chat'):
        self.read_only,self.trace_purpose=read_only,trace_purpose
        self.release_coordinator=release_coordinator
        self.evidence_policy, self.self_check_enabled = evidence_policy, self_check_enabled
        self.observability = observability
        self.tool_service=tool_service
        self.checkpointer, self.store = checkpointer, store
        self._explicit_context_store = context_store is not None
        self.gateway_factory = gateway_factory
        self.retrieve, self.execute, self.record = retrieve, execute, record
        self.locks = SessionLocks()
        if context_store is None:
            from app.session_store import MySQLSessionStore
            if isinstance(store, MySQLSessionStore):
                context_store = ConversationStore()
            else:
                if not hasattr(store, '_context_store'):
                    store._context_store = InMemoryConversationStore()
                context_store = store._context_store
        self._context_store = context_store
        self.summary_manager = summary_manager
        if not hasattr(store,'_context_temporary'):
            store._context_temporary = {}
        self._temporary = store._context_temporary

    @property
    def context_store(self):
        # Existing tests and embedding callers can replace the archive after reopening.
        if not self._explicit_context_store:
            return getattr(self.store,'_context_store',self._context_store)
        return self._context_store

    async def resolve_identity(self, session_id, *, user_id=None, conversation_id=None):
        identity = parse_canonical(session_id)
        if identity:
            if (user_id is not None and user_id != identity.user_id) or (conversation_id is not None and conversation_id != identity.conversation_id):
                raise ValueError('conversation identity mismatch')
            return await asyncio.to_thread(self.context_store.get_owned, identity.conversation_id, identity.user_id)
        if user_id is not None:
            if conversation_id is not None:
                raise ValueError('canonical session_id required')
            async with self.locks.hold('create:'+session_id):
                key=(user_id,session_id)
                if key not in self._temporary:
                    self._temporary[key]=await asyncio.to_thread(self.context_store.create_conversation,user_id)
                return self._temporary[key]
        if conversation_id is not None:
            raise ValueError('user_id required')
        from app.session_store import MySQLSessionStore
        if isinstance(self.store,MySQLSessionStore):
            cid=await asyncio.to_thread(self.store.get_or_create_conversation,session_id)
        else:
            key=('legacy',session_id)
            if key not in self._temporary:
                self._temporary[key]=await asyncio.to_thread(self.context_store.create_conversation,
                    uuid.uuid5(uuid.NAMESPACE_URL,session_id).hex)
            cid=self._temporary[key].conversation_id
        return ConversationIdentity(session_id,cid,session_id,True)

    async def _hydrate(self, old, cid):
        rows=await asyncio.to_thread(self.context_store.read_messages,cid)
        refs=dict(old.get('message_refs',{}))
        messages=list(old.get('messages',[]))
        def restore(row):
            mid=f'db:{row.id}'
            if row.role=='user':
                message=HumanMessage(row.content,id=mid)
            elif row.role=='assistant':
                message=AIMessage(row.content,id=mid,tool_calls=row.tool_calls or [])
            elif row.role=='tool':
                message=ToolMessage(row.content,id=mid,tool_call_id=row.tool_call_id or '')
            else:
                raise ValueError('unknown archived message role')
            refs[mid]={'db_id':row.id,'turn_from_msg_id':row.turn_from_msg_id}
            return message
        if not messages:
            return [restore(row) for row in rows],refs
        def signature(calls):
            return [(c['id'],c['name'],c['args']) for c in calls or []]
        # Old checkpoints are aligned by same-conversation ordered role/content, never array offsets.
        turn=0
        used=set()
        for m in messages:
            ref=refs.get(m.id)
            if m.type=='human':
                matches=[r for r in rows if r.role=='user' and r.content==m.content and r.id>turn and r.id not in used
                         and (ref is None or ref['db_id'] is None or ref['db_id']==r.id)]
                if not matches:
                    raise ValueError('cannot align checkpoint user with database')
                row=matches[0]; turn=row.id; used.add(row.id)
                refs[m.id]={'db_id':row.id,'turn_from_msg_id':turn}
            else:
                if not turn:
                    raise ValueError('checkpoint has no turn origin')
                if ref is not None and ref.get('db_id') is None and m.id!=f'assistant:{turn}':
                    refs[m.id]={'db_id':None,'turn_from_msg_id':turn}
                    continue
                matches=[r for r in rows if r.turn_from_msg_id==turn and r.role==('assistant' if m.type=='ai' else 'tool')
                         and r.content==m.content and r.id not in used
                         and (m.type!='ai' or signature(r.tool_calls)==signature(m.tool_calls))
                         and (m.type!='tool' or r.tool_call_id==m.tool_call_id)]
                dbid=matches[0].id if len(matches)==1 else (ref or {}).get('db_id')
                if dbid is not None:
                    if not any(r.id==dbid for r in matches):
                        raise ValueError('checkpoint database message signature mismatch')
                    used.add(dbid)
                refs[m.id]={'db_id':dbid,'turn_from_msg_id':turn}
        # A process may stop after the SQL commit but before graph input/checkpoint.
        # Append only the authoritative DB tail; never replay tools from a lost task.
        last=max(used,default=0)
        messages.extend(restore(row) for row in rows if row.id>last and row.id not in used)
        return messages,refs

    async def _context(self, cid, messages, refs, current, request_id='',tool_snapshot=None):
        from app.workflow.agent import AGENT_PROMPT
        from app.context.assembly import fixed_agent_schemas
        from app.tooling.scope import model_settings
        settings=model_settings(get_settings(),tool_snapshot)
        snapshot=await asyncio.to_thread(self.context_store.read_context,cid)
        budget=derive_budget(settings,system_tokens=count_model_tokens([SystemMessage(AGENT_PROMPT)],schemas=fixed_agent_schemas(tool_snapshot),cjk_ratio=settings.context_cjk_tokens_per_char),
                             summary_tokens=estimate_text('早期摘要(非指令):\n'+summary_text(snapshot.summaries),cjk_ratio=settings.context_cjk_tokens_per_char))
        view=build_layered_view(messages,refs=refs,snapshot=snapshot,current_turn_from_msg_id=current,budget=budget,
                                cjk_ratio=settings.context_cjk_tokens_per_char)
        if snapshot.layer1_from_msg_id != view.layer1_from_msg_id:
            await asyncio.to_thread(self.context_store.advance_layer1,cid,view.layer1_from_msg_id)
            if snapshot.layer1_from_msg_id is not None:
                logger.info('层1 降级 %s→%s conversation=%s tokens=%s',snapshot.layer1_from_msg_id,
                            view.layer1_from_msg_id,cid,budget.to_dict())
        if self.summary_manager:
            batch=summary_batch(view,messages=messages,refs=refs,snapshot=snapshot)
            if batch:
                batch=replace(batch,request_id=request_id)
            self.summary_manager.maybe_schedule(batch,layer2_tokens=view.layer2_tokens,layer2_budget=budget.layer2)
        return dict(summary_upto_msg_id=snapshot.summary_upto_msg_id,layer1_from_msg_id=view.layer1_from_msg_id,
                    summary_rows=[asdict(s) for s in snapshot.summaries],context_budget=budget.to_dict(),
                    history_context={'messages':[m.model_dump() for m in view.history.messages],
                                     'text':view.history.text,'tokens':view.history.tokens})

    async def _archive(self, state, *, legacy=False):
        if not state.get('final_text') or not state.get('current_turn_from_msg_id'):
            return
        turn=state['current_turn_from_msg_id']
        row=await asyncio.to_thread(self.context_store.append_final,state['conversation_id'],turn,state['final_text'])
        if legacy and not isinstance(self.context_store,ConversationStore):
            history=await asyncio.to_thread(self.store.get_history,state['session_id'])
            if not any(m.id==f'assistant:{turn}' for m in history):
                await asyncio.to_thread(self.store.append_message,state['session_id'],AIMessage(state['final_text'],id=f'assistant:{turn}'))
        refs=dict(state['message_refs'])
        target=next((m for m in reversed(state['messages']) if m.type=='ai' and not m.tool_calls and m.content==state['final_text']),None)
        if target is None:
            raise ValueError('final checkpoint slot missing')
        refs[target.id]={'db_id':row.id,'turn_from_msg_id':turn}
        identity = self.turn_identity(state, assistant_message_id=row.id)
        state['turn_identity'] = identity
        await self.graph().aupdate_state(self.config(state['session_id']),
            {'message_refs':refs, 'turn_identity':identity},as_node='log_result')
        if state.get('request_id') == current_request_id():
            bind_turn(TurnIdentity(**identity))
            update_trace(state)
        return identity

    def turn_identity(self, state, *, request_id=None, assistant_message_id=None):
        request_id = request_id or state['request_id']
        previous = state.get('turn_identity') or {}
        return asdict(TurnIdentity(state['session_id'], state['conversation_id'],
            state['current_turn_from_msg_id'], request_id, request_id,
            assistant_message_id if assistant_message_id is not None else previous.get('assistant_message_id')))

    def config(self, session_id):
        return tracing_config({'configurable': {'thread_id': session_id}, 'recursion_limit': 64})

    def graph(self, model=None):
        return build_workflow(checkpointer=self.checkpointer, deps=WorkflowDeps(
            self.gateway_factory(model), self.store, self.retrieve, self.execute, self.record,self.context_store,self.tool_service,
            self.evidence_policy,self.self_check_enabled,self.release_coordinator,self.read_only))

    async def get_state(self, session_id):
        return (await self.graph().aget_state(self.config(session_id))).values

    async def save_actions(self, session_id, actions):
        graph = self.graph()
        snapshot = await graph.aget_state(self.config(session_id))
        if snapshot.interrupts:
            # Command(update) keeps the native waiter paused; no fabricated completion.
            await graph.ainvoke(Command(update={'actions': actions}), self.config(session_id))
        else:
            await graph.aupdate_state(self.config(session_id), {'actions': actions}, as_node='log_result')

    async def public_session_state(self, session_id):
        if parse_canonical(session_id):
            await self.resolve_identity(session_id)
        async with self.locks.hold(session_id):
            snapshot = await self.graph().aget_state(self.config(session_id))
            state = snapshot.values
            selection = state.get('selection')
            visible = None
            if selection and snapshot.interrupts and selection['status'] == 'available':
                visible = snapshot.interrupts[0].value
            from app.workflow.tool_calls import public_preview
            confirmation=state.get('ticket_confirmation')
            ticket=public_preview(confirmation) if confirmation else None
            if ticket and ticket['status'] in {'creating','confirmed'}:
                ticket['status']='uncertain'
            if ticket and ticket['status']=='available' and (not snapshot.interrupts or time.time()>confirmation['expires_at']):
                ticket['status']='expired'
            if ticket and confirmation.get('result'):
                ticket['result']=confirmation['result']
            return {'status': state.get('status', 'empty'), 'selection': visible,'ticket_confirmation':ticket,
                    'actions': self._action_items(state, all_actions=True)}

    def _action_items(self, state, *, all_actions=False):
        labels = {'handoff':'转人工', 'create_ticket':'建工单', 'refund_application':'提交演示退款申请'}
        actions = state.get('actions', {})
        ids = list(actions) if all_actions else state.get('turn_action_ids', [])
        return [{**actions[key], 'label':labels[actions[key]['kind']]} for key in ids if key in actions]

    async def validate_tool_confirmation(self,request):
        from app.workflow.ticket_confirmation import TicketConfirmationCoordinator
        await TicketConfirmationCoordinator(self).preflight(request)

    async def stream_tool_confirmation(self,request,*,model):
        from app.workflow.ticket_confirmation import TicketConfirmationCoordinator
        async with aclosing(TicketConfirmationCoordinator(self).stream(request,model=model)) as stream:
            async for event in stream:yield event

    def _check_selection(self, snapshot, request):
        from app.workflow.actions import ActionError
        state = snapshot.values
        selection = state.get('selection')
        if (not snapshot.interrupts or not selection or selection['status'] != 'available'
                or selection['selection_id'] != request.selection_id):
            raise ActionError('此订单选择已处理或失效，请查看最新消息。', 409)
        if request.order_id not in {row['order_id'] for row in selection['candidates']}:
            raise ActionError('该订单不在可选列表内。', 422)
        if snapshot.interrupts[0].value.get('selection_id') != request.selection_id:
            raise ActionError('待处理订单选择已变化。', 409)
        return state

    async def validate_order_selection(self, request):
        if parse_canonical(request.session_id):
            await self.resolve_identity(request.session_id)
        async with self.locks.hold(request.session_id):
            snapshot = await self.graph().aget_state(self.config(request.session_id))
            self._check_selection(snapshot, request)

    @traced_stream('order-selection')
    @knowledge_turn_stream
    async def stream_order_selection(self, request, *, model):
        from app.workflow.actions import ActionError
        async with self.locks.hold(request.session_id):
            graph = self.graph(model)
            request_id = current_request_id() or uuid.uuid4().hex
            started = False
            completed = False
            try:
                snapshot = await graph.aget_state(self.config(request.session_id))
                state = self._check_selection(snapshot, request)
                turn = self.turn_identity(state, request_id=request_id) if state.get('current_turn_from_msg_id') else None
                if turn:
                    bind_turn(TurnIdentity(**turn))
                    yield {'type':'turn', **turn}
                old_ids = {message.id for message in state.get('messages', [])}
                budget = new_budget(contextual=True)
                ctx={}
                if state.get('current_turn_from_msg_id'):
                    ctx=await self._context(state['conversation_id'],state['messages'],state['message_refs'],
                                            state['current_turn_from_msg_id'],request_id,state.get('tool_snapshot'))
                data = Command(resume={'selection_id':request.selection_id, 'order_id':request.order_id},
                    update={**ctx,'budget':budget.model_dump(), 'request_id':request_id, 'status':'running',
                            'continuation_of':state['request_id'], 'turn_identity':turn,
                            'usage_segments':[*state.get('usage_segments', []),
                                              {'request_id':state['request_id'], 'budget':state['budget']}]})
                started = True
                async with asyncio.timeout(max(.001, budget.deadline-time.time())):
                    async with aclosing(graph.astream(data, self.config(request.session_id), stream_mode=['custom','updates'])) as stream:
                        async for kind, event in stream:
                            if kind == 'custom':
                                yield event
                final = (await graph.aget_state(self.config(request.session_id))).values
                if final.get('current_turn_from_msg_id'):
                    archived = await self._archive(final,legacy=parse_canonical(request.session_id) is None)
                    if archived:
                        yield {'type':'turn', **archived}
                else:
                    for message in final.get('messages', []):
                        if message.id not in old_ids:
                            await asyncio.to_thread(self.store.append_message, request.session_id, message)
                completed = True
                items = self._action_items(final)
                if items:
                    yield {'type':'actions', 'items':items}
                yield {'type':'done'}
            except asyncio.CancelledError:
                raise
            except ActionError as exc:
                yield {'type':'error', 'message':str(exc)}
            except Exception as exc:
                logger.warning('workflow resume failed request=%s exception=%s', request_id, type(exc).__name__)
                yield {'type':'error', 'message':'订单处理未完成，请重新发起请求或联系人工客服。'}
            finally:
                if started and not completed:
                    await asyncio.shield(self._interrupt(request.session_id, request_id))

    async def _interrupt(self, session_id, request_id):
        state = await self.get_state(session_id)
        if state.get('request_id') != request_id:
            return
        actions = dict(state.get('actions', {}))
        for key in state.get('turn_action_ids', []):
            if key in actions and actions[key]['status'] == 'available':
                actions[key] = {**actions[key], 'status': 'interrupted'}
        await self.graph().aupdate_state(self.config(session_id), {'status': 'interrupted', 'actions': actions}, as_node='log_result')

    @traced_stream('chat')
    @knowledge_turn_stream
    async def stream_turn(self, session_id: str, question: str, *, model, user_id=None, conversation_id=None):
        try:
            identity=await self.resolve_identity(session_id,user_id=user_id,conversation_id=conversation_id)
        except (ValueError,LookupError):
            yield {'type':'error','message':'会话身份无效或不存在。'}
            return
        session_id=identity.session_id
        async with self.locks.hold(session_id):
            request_id = current_request_id() or uuid.uuid4().hex
            completed = False
            try:
                old = await self.get_state(session_id)
                if (old.get('ticket_confirmation') or {}).get('status')=='available' and self.tool_service:
                    from app.workflow.tool_calls import reject_ticket
                    expired=await reject_ticket(old,self.tool_service,status='expired')
                    refs=dict(old.get('message_refs',{}))
                    for message in expired['messages']:
                        message.id='expired:'+message.tool_call_id
                        refs[message.id]={'db_id':None,'turn_from_msg_id':old['current_turn_from_msg_id']}
                    await self.graph().aupdate_state(self.config(session_id),{**expired,'message_refs':refs},as_node='agent_tools')
                    old=await self.get_state(session_id)
                tool_snapshot=await self.tool_service.refresh() if self.tool_service is not None else None
                conversation_id = identity.conversation_id
                if old.get('final_text') and old.get('current_turn_from_msg_id'):
                    await self._archive(old,legacy=identity.legacy)
                history,refs = await self._hydrate(old,conversation_id)
                settings=get_settings()
                if estimate_text(question,cjk_ratio=settings.context_cjk_tokens_per_char)>settings.max_user_input_tokens:
                    raise ContextBudgetError('当前输入超过单轮预算')
                row=await asyncio.to_thread(self.context_store.append_user,conversation_id,question)
                bind_turn(TurnIdentity(session_id, conversation_id, row.id, request_id, request_id))
                human = HumanMessage(content=question, id=f'db:{row.id}')
                refs[human.id]={'db_id':row.id,'turn_from_msg_id':row.id}
                if identity.legacy and not isinstance(self.context_store,ConversationStore):
                    await asyncio.to_thread(self.store.append_message,session_id,human)
                if not identity.legacy:
                    yield {'type':'conversation','user_id':identity.user_id,'session_id':session_id,'conversation_id':conversation_id}
                ctx=await self._context(conversation_id,[*history,human],refs,row.id,request_id,tool_snapshot)
                # New input restarts at START; never resume a crashed tool task.
                budget = new_budget(contextual=True)
                data = {'messages': [*history, human], 'session_id': session_id, 'conversation_id': conversation_id,
                        'message_refs':refs,'current_turn_from_msg_id':row.id,**ctx,
                        'request_id': request_id, 'raw_question': question, 'resolved_question': question,
                        'intent': '', 'route': '', 'retrieval': None, 'gate_reason': '', 'budget': budget.model_dump(),
                        'gap_source':'retrieval_low_conf','evidence_allowed':None,'evidence_checked_hash':None,
                        'evidence_confidence':None,'sufficiency':None,
                        'maintenance_reason':None,
                        'status': 'running', 'pending_decision': None, 'control': None,
                        'actions': old.get('actions', {}), 'turn_action_ids': [], 'node_trace': [], 'final_text': ''}
                data.update(confidence=0.0, understanding={}, selected_order_id=None, order=None, selection=None,
                            tool_snapshot=asdict(tool_snapshot) if tool_snapshot is not None else None,
                            tool_failure=None,ticket_confirmation=None,ticket_flow_closed=False,
                            verified_reference_order_id=None,
                            expansion_queries=[], expansion_fallback=False, continuation_of=None, usage_segments=[])
                data['turn_identity'] = self.turn_identity(data)
                bind_turn(TurnIdentity(**data['turn_identity']))
                yield {'type':'turn', **data['turn_identity']}
                graph = self.graph(model)
                async with asyncio.timeout(max(0.001, budget.deadline - time.time())):
                    async with aclosing(graph.astream(data, self.config(session_id), stream_mode=['custom','updates'],durability='sync')) as stream:
                        async for kind, event in stream:
                            if kind == 'custom':
                                yield event
                snapshot = await graph.aget_state(self.config(session_id))
                state = snapshot.values
                if not snapshot.interrupts:
                    archived = await self._archive(state,legacy=identity.legacy)
                    if archived:
                        yield {'type':'turn', **archived}
                completed = True
                if snapshot.interrupts:
                    yield snapshot.interrupts[0].value
                    yield {'type':'done'}
                    return
                items = self._action_items(state)
                if items:
                    yield {'type': 'actions', 'items': items}
                yield {'type': 'done'}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('workflow interrupted request=%s exception=%s', request_id, type(exc).__name__)
                message = (str(exc) if isinstance(exc,ContextBudgetError) else
                           exc.public_message if isinstance(exc, EmbeddingError) else
                           '重排服务暂时不可用，请稍后重试或联系人工客服。' if isinstance(exc, RerankError) else
                           '处理消息时出现错误，请稍后重试或联系人工客服。')
                yield {'type': 'error', 'message': message}
            finally:
                if not completed:
                    try:
                        await asyncio.shield(self._interrupt(session_id, request_id))
                    except Exception as exc:
                        logger.warning('workflow checkpoint interrupt failed exception=%s', type(exc).__name__)
