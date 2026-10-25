from app.context.store import InMemoryConversationStore
from app.history import InMemorySessionStore
from app.workflow.runtime import WorkflowRuntime, open_checkpointer
from tests.test_workflow_graph import Gateway


async def test_committed_db_tail_recovers_after_checkpoint_gap(tmp_path):
    db=InMemoryConversationStore()
    identity=db.create_conversation('f'*32)
    first=db.append_user(identity.conversation_id,'第一条')
    final=db.append_final(identity.conversation_id,first.id,'已回答')
    orphan=db.append_user(identity.conversation_id,'已写库但checkpoint前崩溃')
    from langchain_core.messages import HumanMessage,AIMessage
    old={'messages':[HumanMessage(first.content,id='u'),AIMessage(final.content,id='a')]}
    async with open_checkpointer(str(tmp_path/'gap.sqlite')) as saver:
        runtime=WorkflowRuntime(saver,InMemorySessionStore(100),context_store=db)
        messages,refs=await runtime._hydrate(old,identity.conversation_id)
        assert [m.content for m in messages]==[first.content,final.content,orphan.content]
        assert refs[messages[-1].id]['db_id']==orphan.id


async def test_legacy_tool_rows_restore_metadata_without_final_slot_collision(tmp_path):
    from app.context.contracts import StoredMessage
    from langchain_core.messages import ToolMessage
    class Rows:
        def read_messages(self,cid):
            return [StoredMessage(10,cid,'user','查订单1001',10),
                    StoredMessage(12,cid,'assistant','',10,tool_calls=[dict(id='call1',name='query_order',args={'order_id':'1001'})]),
                    StoredMessage(15,cid,'tool','结果',10,tool_call_id='call1'),
                    StoredMessage(20,cid,'assistant','已答复',10)]
    async with open_checkpointer(str(tmp_path/'legacy.sqlite')) as saver:
        runtime=WorkflowRuntime(saver,InMemorySessionStore(100),context_store=Rows())
        messages,refs=await runtime._hydrate({},1)
        assert len({m.id for m in messages})==4
        assert messages[1].tool_calls[0]['id']=='call1'
        assert isinstance(messages[2],ToolMessage) and messages[2].tool_call_id=='call1'
        assert refs[messages[2].id]['db_id']==15


async def test_plain_decision_cannot_claim_equal_visible_final_db_row(tmp_path):
    from langchain_core.messages import HumanMessage,AIMessage
    db=InMemoryConversationStore();cid=db.create_conversation('e'*32).conversation_id
    user=db.append_user(cid,'你好');final=db.append_final(cid,user.id,'您好')
    messages=[HumanMessage('你好',id=f'db:{user.id}'),AIMessage('您好',id='internal:decision'),
              AIMessage('您好',id=f'assistant:{user.id}')]
    refs={m.id:dict(db_id=d,turn_from_msg_id=user.id) for m,d in zip(messages,[user.id,None,final.id])}
    async with open_checkpointer(str(tmp_path/'equal.sqlite')) as saver:
        runtime=WorkflowRuntime(saver,InMemorySessionStore(100),context_store=db)
        _,actual=await runtime._hydrate({'messages':messages,'message_refs':refs},cid)
        assert actual['internal:decision']['db_id'] is None
        assert actual[f'assistant:{user.id}']['db_id']==final.id


async def test_final_archive_failure_recovers_before_next_user_commit(tmp_path):
    class FailingArchive(InMemoryConversationStore):
        fail=True
        def append_final(self,*args):
            if self.fail:
                raise RuntimeError('synthetic pre-commit outage')
            return super().append_final(*args)
    db=FailingArchive()
    async with open_checkpointer(str(tmp_path/'archive.sqlite')) as saver:
        runtime=WorkflowRuntime(saver,InMemorySessionStore(100),context_store=db,gateway_factory=lambda m:m)
        events=[e async for e in runtime.stream_turn('tmp','第一轮',model=Gateway('闲聊'),user_id='d'*32)]
        meta=events[0];cid=meta['conversation_id'];sid=meta['session_id']
        assert events[-1]['type']=='error'
        old=await runtime.get_state(sid);original=old['final_text'];turn=old['current_turn_from_msg_id']
        assert old['status']=='interrupted' and original
        assert [m.role for m in db.read_messages(cid)]==['user']
        # A second failure must not commit a new user beyond the missing final slot.
        events=[e async for e in runtime.stream_turn(sid,'仍然故障',model=Gateway('闲聊'))]
        assert events[-1]['type']=='error'
        assert [m.role for m in db.read_messages(cid)]==['user']
        db.fail=False
        events=[e async for e in runtime.stream_turn(sid,'恢复后继续',model=Gateway('闲聊'))]
        assert events[-1]['type']=='done'
        rows=db.read_messages(cid)
        assert [m.role for m in rows]==['user','assistant','user','assistant']
        assert rows[1].content==original
        state=await runtime.get_state(sid)
        assert state['message_refs'][f'assistant:{turn}']['db_id']==rows[1].id


async def test_native_ids_full_checkpoint_and_reopen(tmp_path):
    path=str(tmp_path/'ctx.sqlite'); db=InMemoryConversationStore(); archive=InMemorySessionStore(100)
    async with open_checkpointer(path) as saver:
        runtime=WorkflowRuntime(saver,archive,context_store=db,gateway_factory=lambda m:m)
        events=[e async for e in runtime.stream_turn('temp-a','订单ORD-1001',model=Gateway(),user_id='a'*32)]
        meta=next(e for e in events if e['type']=='conversation')
        sid=meta['session_id']; cid=meta['conversation_id']
        state=await runtime.get_state(sid)
        user=next(m for m in state['messages'] if m.type=='human')
        assert user.id.startswith('db:')
        assert state['messages'][-1].id==f"assistant:{state['current_turn_from_msg_id']}"
        assert len(state['message_refs'])==len(state['messages'])
        assert [m.role for m in db.read_messages(cid)]==['user','assistant']
    async with open_checkpointer(path) as saver:
        runtime=WorkflowRuntime(saver,archive,context_store=db,gateway_factory=lambda m:m)
        for i in range(21):
            events=[e async for e in runtime.stream_turn(sid,f'你好{i}',model=Gateway('闲聊'),user_id='a'*32,conversation_id=cid)]
            assert events[-1]['type']=='done'
        state=await runtime.get_state(sid)
        assert len([m for m in state['messages'] if m.type=='human'])==22
        assert not db.read_context(cid).summaries
        assert state['layer1_from_msg_id']==int(user.id[3:])


async def test_recover_final_slot_with_missing_checkpoint_db_metadata(tmp_path):
    db=InMemoryConversationStore(); archive=InMemorySessionStore(100)
    async with open_checkpointer(str(tmp_path/'recover.sqlite')) as saver:
        runtime=WorkflowRuntime(saver,archive,context_store=db,gateway_factory=lambda m:m)
        events=[e async for e in runtime.stream_turn('temp','你好',model=Gateway('闲聊'),user_id='b'*32)]
        meta=events[0]; sid=meta['session_id']; cid=meta['conversation_id']
        state=await runtime.get_state(sid)
        final=state['messages'][-1]
        refs=dict(state['message_refs']); refs[final.id]={**refs[final.id],'db_id':None}
        await runtime.graph().aupdate_state(runtime.config(sid),{'message_refs':refs},as_node='log_result')
        events=[e async for e in runtime.stream_turn(sid,'谢谢',model=Gateway('闲聊'))]
        assert events[-1]['type']=='done'
        state=await runtime.get_state(sid)
        assert state['message_refs'][final.id]['db_id'] is not None
        assert len(db.read_messages(cid))==4


async def test_background_cannot_block_foreground_or_overwrite_checkpoint(tmp_path,monkeypatch):
    from app.config import get_settings
    from app.context.summary import SummaryManager
    from tests.test_context_summary import Blocking
    settings=get_settings().model_copy(update={'context_target_turns':1,'context_steady_turn_tokens':80})
    monkeypatch.setattr('app.workflow.runtime.get_settings',lambda:settings)
    db=InMemoryConversationStore(); model=Blocking()
    manager=SummaryManager(db,model,settings=settings)
    async with open_checkpointer(str(tmp_path/'async.sqlite')) as saver:
        runtime=WorkflowRuntime(saver,InMemorySessionStore(100),context_store=db,summary_manager=manager,gateway_factory=lambda m:m)
        for i in range(3):
            events=[e async for e in runtime.stream_turn('temp','订单ORD-1001没收到，需要查物流'+str(i),model=Gateway(),user_id='c'*32)]
            assert events[-1]['type']=='done'
        assert model.started.is_set() and not model.release.is_set()
        meta=events[0]; before=await runtime.get_state(meta['session_id'])
        model.release.set()
        import asyncio
        await asyncio.gather(*manager.tasks.values())
        after=await runtime.get_state(meta['session_id'])
        assert before['messages']==after['messages']
        assert before['message_refs']==after['message_refs']
        await manager.aclose()
