"""Schema binding, budget estimation and concurrent turns share the exact same snapshot."""
import asyncio
from dataclasses import replace
from langchain_core.messages import AIMessage,AIMessageChunk,SystemMessage,HumanMessage
from app.context.assembly import fixed_agent_schemas
from app.context.budget import derive_budget
from app.context.tokens import count_model_tokens
from app.config import get_settings
from app.tooling.scope import tool_scope
from app.workflow.agent import new_budget
from app.workflow.models import ModelGateway
from tests.test_tool_integration import make_service


async def test_model_decide_answer_bind_same_snapshot_with_dynamic_tool_and_ticket(tmp_path):
    service,_,_=make_service(tmp_path)
    await service.start()
    snapshot=await service.refresh()
    class Model:
        def __init__(self): self.bound=[]
        def bind_tools(self,tools,**kwargs):
            self.bound.append((tools,kwargs))
            return self
        async def ainvoke(self,messages,**kwargs):
            return AIMessage(content="",tool_calls=[{"name":"respond_to_user","args":{"response_mode":"answer"},"id":"r"}])
        async def astream(self,messages,**kwargs):
            yield AIMessageChunk(content="结果")
    model=Model()
    messages=[SystemMessage("客服"),HumanMessage("查询")]
    try:
        with tool_scope(service,snapshot,{}):
            schemas=fixed_agent_schemas()
            budget=new_budget(contextual=True)
            gateway=ModelGateway(model)
            await gateway.decide(messages,budget)
            _=[c async for c in gateway.stream_answer(messages,budget)]
        names={s["function"]["name"] for s in schemas}
        assert {"mcp__vendor__lookup","create_ticket","respond_to_user"}<=names
        assert model.bound[0][0]==model.bound[1][0]==schemas
        assert model.bound[1][1]["tool_choice"]=="none"
    finally:
        await service.aclose()


async def test_schema_snapshots_are_scoped_per_concurrent_task_and_cost_real_tokens(tmp_path):
    service,_,_=make_service(tmp_path)
    await service.start()
    first=await service.refresh()
    added={"type":"function","function":{"name":"long","description":"字"*1800,"parameters":{"type":"object"}}}
    second=replace(first,version="other",schemas=(*first.schemas,added))
    entered=asyncio.Event()
    async def view(snapshot):
        with tool_scope(service,snapshot,{}):
            entered.set()
            await entered.wait()
            await asyncio.sleep(0)
            return fixed_agent_schemas()
    try:
        a,b=await asyncio.gather(view(first),view(second))
        assert "long" not in {s["function"]["name"] for s in a}
        assert "long" in {s["function"]["name"] for s in b}
        settings=get_settings().model_copy(update={"model_context_window":50000,"context_system_reserve_tokens":1})
        ca=count_model_tokens([SystemMessage("客服")],schemas=a)
        cb=count_model_tokens([SystemMessage("客服")],schemas=b)
        assert cb>ca
        assert derive_budget(settings,system_tokens=cb,summary_tokens=0).layer1 < derive_budget(settings,system_tokens=ca,summary_tokens=0).layer1
    finally:
        await service.aclose()

