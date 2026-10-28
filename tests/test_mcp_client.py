"""MCP discovery boundaries against a controlled external transport."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import json
import pytest
from app.tooling.mcp_client import MCPManager
from app.tooling.policy import PolicyLoader


def definition(name):
    return SimpleNamespace(name=name,description="查询演示",inputSchema={"type":"object","properties":{"id":{"type":"string"}}})


def policy(tmp_path):
    path=tmp_path/"policy.json"
    path.write_text(json.dumps({"servers":{"vendor":{"url":"http://127.0.0.1:8021/mcp","enabled":True}},"rules":{}}))
    return PolicyLoader(path).load()


class Session:
    def __init__(self,pages):
        self.pages=pages
        self.owner=None
    async def initialize(self):
        self.owner=asyncio.current_task()
    async def list_tools(self,*,params=None):
        assert asyncio.current_task() is self.owner
        cursor=params.cursor if params else None
        return self.pages[cursor]
    async def call_tool(self,name,args):
        assert asyncio.current_task() is self.owner
        return SimpleNamespace(isError=False,structuredContent={"id":args["id"]},content=[])


async def test_all_pages_are_discovered_and_same_owner_handles_calls(tmp_path):
    session=Session({None:SimpleNamespace(tools=[definition("one")],nextCursor="next"),
                    "next":SimpleNamespace(tools=[definition("two")],nextCursor=None)})
    closed=[]
    @asynccontextmanager
    async def connect(url):
        try:
            yield session
        finally:
            closed.append(asyncio.current_task())
    manager=MCPManager(connect=connect)
    await manager.start()
    try:
        found=await manager.discover(policy(tmp_path))
        assert [t.name for t in found["vendor"]]==["one","two"]
        result=await manager.call("vendor","one",{"id":"1001"},deadline=asyncio.get_running_loop().time()+2)
        assert result.content=={"id":"1001"}
    finally:
        await manager.aclose()
    assert closed==[session.owner]


@pytest.mark.parametrize("mode",["repeat","too_many","invalid","oversized_description"])
async def test_invalid_directory_never_publishes_partial_source(tmp_path,mode):
    tools=[definition("one")]
    cursor=None
    if mode=="too_many":
        tools=[definition("t"+str(i)) for i in range(101)]
    elif mode=="invalid":
        tools.append(definition("bad name"))
    elif mode=="oversized_description":
        tools[0].description="字"*2001
    else:
        cursor="again"
    session=Session({None:SimpleNamespace(tools=tools,nextCursor=cursor),
                     "again":SimpleNamespace(tools=[definition("two")],nextCursor="again")})
    @asynccontextmanager
    async def connect(url):
        yield session
    manager=MCPManager(connect=connect)
    try:
        assert await manager.discover(policy(tmp_path))=={}
        assert "vendor" in manager.diagnostics
    finally:
        await manager.aclose()


async def test_page_limit_does_not_silently_publish_twenty_page_prefix(tmp_path):
    pages={None:SimpleNamespace(tools=[definition("start")],nextCursor="1")}
    for index in range(1,21):
        pages[str(index)]=SimpleNamespace(tools=[definition("p"+str(index))],nextCursor=str(index+1))
    session=Session(pages)
    @asynccontextmanager
    async def connect(url):
        yield session
    manager=MCPManager(connect=connect)
    try:
        assert await manager.discover(policy(tmp_path))=={}
    finally:
        await manager.aclose()


async def test_one_unavailable_source_does_not_hide_other_source(tmp_path):
    path=tmp_path/"two.json"
    path.write_text(json.dumps({"servers":{"good":{"url":"http://127.0.0.1:8021/mcp","enabled":True},
        "bad":{"url":"http://127.0.0.1:8022/mcp","enabled":True}},"rules":{}}))
    session=Session({None:SimpleNamespace(tools=[definition("one")],nextCursor=None)})
    @asynccontextmanager
    async def connect(url):
        if ":8022/" in url:
            raise ConnectionError("temporarily unavailable")
        yield session
    manager=MCPManager(connect=connect)
    try:
        found=await manager.discover(PolicyLoader(path).load())
        assert set(found)=={"good"}
        assert manager.diagnostics["bad"]=="ConnectionError"
    finally:
        await manager.aclose()


async def test_discovery_timeout_reconnects_once_and_leaves_no_owner(tmp_path):
    entered=[]
    @asynccontextmanager
    async def connect(url):
        entered.append(asyncio.current_task())
        class Blocked:
            async def initialize(self):
                await asyncio.Event().wait()
        yield Blocked()
    manager=MCPManager(connect=connect,discovery_timeout=0.02)
    try:
        assert await manager.discover(policy(tmp_path))=={}
        assert len(entered)==2
    finally:
        await manager.aclose()
    assert all(task.done() for task in entered)


async def test_endpoint_change_closes_old_owner_before_new_connection(tmp_path):
    events=[]
    @asynccontextmanager
    async def connect(url):
        events.append(("open",url))
        try:
            yield Session({None:SimpleNamespace(tools=[definition("one")],nextCursor=None)})
        finally:
            events.append(("close",url))
    manager=MCPManager(connect=connect)
    first=policy(tmp_path)
    from dataclasses import replace
    second=replace(first,servers={"vendor":{"url":"http://127.0.0.1:8022/mcp","enabled":True}})
    try:
        await manager.discover(first)
        await manager.discover(second)
        assert events[:3]==[("open","http://127.0.0.1:8021/mcp"),("close","http://127.0.0.1:8021/mcp"),("open","http://127.0.0.1:8022/mcp")]
    finally:
        await manager.aclose()


async def test_closing_owner_wakes_active_request_immediately(tmp_path):
    entered=asyncio.Event()
    class BlockedCall(Session):
        async def call_tool(self,name,args):
            entered.set()
            await asyncio.Event().wait()
    session=BlockedCall({None:SimpleNamespace(tools=[definition("one")],nextCursor=None)})
    @asynccontextmanager
    async def connect(url):
        yield session
    manager=MCPManager(connect=connect)
    await manager.discover(policy(tmp_path))
    request=asyncio.create_task(manager.call("vendor","one",{"id":"1001"},deadline=asyncio.get_running_loop().time()+30))
    await entered.wait()
    await manager.aclose()
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(request,0.2)


async def test_text_json_empty_result_is_not_found_not_service_error(tmp_path):
    from mcp.types import TextContent
    class TextSession(Session):
        async def call_tool(self,name,args):
            return SimpleNamespace(isError=False,structuredContent=None,content=[TextContent(type="text",text='{"found":false}')])
    session=TextSession({None:SimpleNamespace(tools=[definition("one")],nextCursor=None)})
    @asynccontextmanager
    async def connect(url):
        yield session
    manager=MCPManager(connect=connect)
    try:
        await manager.discover(policy(tmp_path))
        result=await manager.call("vendor","one",{},deadline=asyncio.get_running_loop().time()+3)
        assert result.outcome=="not_found" and result.content=={"found":False}
    finally:
        await manager.aclose()

