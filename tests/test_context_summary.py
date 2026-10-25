import asyncio
import pytest
from langchain_core.messages import HumanMessage, AIMessage
from app.config import get_settings
from app.context.contracts import SummaryBatch
from app.context.store import InMemoryConversationStore
from app.context.summary import SummaryManager, SummaryCandidate, CoverageCheck, entities_valid


def batch_fixture(db):
    cid = db.create_conversation('c'*32).conversation_id
    a=db.append_user(cid,'订单ORD-1001没收到，需要查物流')
    b=db.append_final(cid,a.id,'尚待查询')
    c=db.append_user(cid,'现在问题')
    db.advance_layer1(cid,c.id)
    return SummaryBatch(cid,None,a.id,b.id,c.id,
        (HumanMessage(a.content,id=f'db:{a.id}'),AIMessage(b.content,id=f'db:{b.id}')),())


class Blocking:
    def __init__(self):
        self.started, self.release = asyncio.Event(), asyncio.Event()
    async def summarize(self,batch):
        self.started.set()
        await self.release.wait()
        return SummaryCandidate(content='订单ORD-1001没收到，需要查物流，尚待查询。')
    async def verify(self,batch,candidate):
        return CoverageCheck(grounded=True,complete=True,missing=[])


async def test_schedule_is_nonblocking_singleflight_and_commits(caplog):
    db=InMemoryConversationStore(); batch=batch_fixture(db); model=Blocking()
    manager=SummaryManager(db,model,settings=get_settings())
    assert manager.maybe_schedule(batch,layer2_tokens=300,layer2_budget=100)
    assert not manager.maybe_schedule(batch,layer2_tokens=300,layer2_budget=100)
    await asyncio.wait_for(model.started.wait(),1)
    assert db.read_context(batch.conversation_id).summary_upto_msg_id is None
    # Foreground may move its boundary; the background must not undo it.
    newer=db.append_user(batch.conversation_id,'更新一轮')
    db.advance_layer1(batch.conversation_id,newer.id)
    model.release.set()
    await asyncio.gather(*manager.tasks.values())
    snapshot=db.read_context(batch.conversation_id)
    assert snapshot.summary_upto_msg_id == batch.upto_msg_id
    assert snapshot.layer1_from_msg_id == newer.id
    assert len(snapshot.summaries)==1
    await manager.aclose()


async def test_cancel_and_failure_leave_boundary_untouched():
    db=InMemoryConversationStore(); batch=batch_fixture(db); model=Blocking()
    manager=SummaryManager(db,model,settings=get_settings())
    manager.maybe_schedule(batch,layer2_tokens=300,layer2_budget=100)
    await model.started.wait()
    await manager.aclose()
    assert not db.read_context(batch.conversation_id).summaries
    class Broken(Blocking):
        async def summarize(self,batch):
            raise RuntimeError('provider failure')
    manager=SummaryManager(db,Broken(),settings=get_settings())
    manager.maybe_schedule(batch,layer2_tokens=300,layer2_budget=100)
    await asyncio.gather(*manager.tasks.values())
    assert db.read_context(batch.conversation_id).summary_upto_msg_id is None


def test_entities_bidirectional_and_not_old_background():
    db=InMemoryConversationStore(); batch=batch_fixture(db)
    assert entities_valid(batch,'订单ORD-1001没收到，需要查物流。')
    assert not entities_valid(batch,'用户没收到，需要查物流。')
    assert not entities_valid(batch,'订单ORD-1001和ORD-9999已退款。')


def test_numeric_order_ids_are_required_and_not_invented():
    batch=SummaryBatch(1,None,1,2,3,(HumanMessage('订单1001未收到，请核实签收人'),),())
    assert entities_valid(batch,'订单1001未收到，需核实签收人。')
    assert not entities_valid(batch,'未收到，需核实签收人。')
    assert not entities_valid(batch,'订单1001和1002未收到。')


async def test_competing_commit_is_skipped():
    db=InMemoryConversationStore(); batch=batch_fixture(db); model=Blocking()
    model.release.set()
    db.append_summary(batch.conversation_id,expected_upto=None,from_msg_id=batch.from_msg_id,
        upto_msg_id=batch.upto_msg_id,content='订单ORD-1001待查。')
    manager=SummaryManager(db,model,settings=get_settings())
    manager.maybe_schedule(batch,layer2_tokens=300,layer2_budget=100)
    await asyncio.gather(*manager.tasks.values())
    assert len(db.read_context(batch.conversation_id).summaries)==1


async def test_capacity_splits_complete_turns_and_restarts_after_partial_commit():
    db=InMemoryConversationStore(); first=batch_fixture(db)
    second_user=db.read_messages(first.conversation_id)[-1]
    second_final=db.append_final(first.conversation_id,second_user.id,'尚未解决')
    current=db.append_user(first.conversation_id,'本轮')
    db.advance_layer1(first.conversation_id,current.id)
    messages=(*first.messages,HumanMessage(second_user.content,id=f'db:{second_user.id}'),
              AIMessage(second_final.content,id=f'db:{second_final.id}'))
    from dataclasses import replace
    batch=replace(first,upto_msg_id=second_final.id,captured_layer1_from=current.id,messages=messages)
    class Split(Blocking):
        async def summarize(self,batch):
            if len(batch.messages)>2:
                return SummaryCandidate(content='')
            return SummaryCandidate(content='订单ORD-1001没收到，需要查物流，尚待查询。' if batch.from_msg_id==first.from_msg_id else '本批继续对话，问题尚未解决。')
    model=Split(); manager=SummaryManager(db,model,settings=get_settings())
    manager.maybe_schedule(batch,layer2_tokens=300,layer2_budget=100)
    await asyncio.gather(*manager.tasks.values())
    rows=db.read_context(first.conversation_id).summaries
    assert [r.seq for r in rows]==[1,2]
    assert rows[0].upto_msg_id==first.upto_msg_id and rows[1].from_msg_id==second_user.id


async def test_different_conversations_run_concurrently():
    db=InMemoryConversationStore(); a=batch_fixture(db); b=batch_fixture(db); model=Blocking()
    manager=SummaryManager(db,model,settings=get_settings())
    assert manager.maybe_schedule(a,layer2_tokens=300,layer2_budget=100)
    assert manager.maybe_schedule(b,layer2_tokens=300,layer2_budget=100)
    assert len(manager.tasks)==2
    await manager.aclose()
