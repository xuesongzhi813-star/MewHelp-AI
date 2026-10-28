"""Application-owned tool catalog, unified engine and MCP lifecycle."""
import asyncio
import time
import uuid
from dataclasses import replace

from app.tooling.audit import MySQLAuditSink
from app.tooling.builtin import register_builtins
from app.tooling.capabilities import CapabilityStore
from app.tooling.engine import ToolEngine
from app.tooling.mcp_client import MCPManager
from app.tooling.registry import ToolRegistry
from app.tooling.contracts import ToolCall,ExecutionContext,ToolExecutionResult
from app.tooling.scope import current_tools,snapshot_object


class ToolService:
    def __init__(self,policy_loader,*,registry=None,manager=None,audit_sink=None):
        self.policy_loader=policy_loader
        self.registry=registry if registry is not None else ToolRegistry()
        if registry is None:
            register_builtins(self.registry)
        self.manager=manager if manager is not None else MCPManager()
        self.capabilities=CapabilityStore()
        self.engine=ToolEngine(self.registry,policy_loader,audit_sink if audit_sink is not None else MySQLAuditSink(),self.capabilities)
        self._refresh_lock=asyncio.Lock()
        self._sources=set()
        self.loop=None

    async def start(self):
        self.loop=asyncio.get_running_loop()
        await self.manager.start()

    def execution_input(self,name,args,*,context=None,tool_call_id=None,snapshot=None):
        _,active,state=current_tools()
        snapshot=snapshot_object(snapshot) or active or self.registry.snapshot(self.policy_loader.load())
        context={**(context or {}),**{key:state[key] for key in ('session_id','conversation_id','request_id') if key in state}}
        request_id=context.get('request_id') or uuid.uuid4().hex
        call_id=tool_call_id or uuid.uuid4().hex
        session_id=context.get('session_id','')
        key=uuid.uuid5(uuid.NAMESPACE_URL,f'{session_id}:{request_id}:{call_id}').hex
        from app.config import get_settings
        settings=get_settings()
        timeout=settings.knowledge_tool_timeout if name=='query_faq' else settings.tool_timeout
        deadline=context.get('deadline',state.get('budget',{}).get('deadline',time.time()+timeout))
        trusted={**context.get('trusted',{}),'tool_timeout':timeout,'tool_call_id':call_id}
        if context.get('query_plan') is not None:
            trusted['query_plan']=context['query_plan']
        ctx=ExecutionContext(session_id,context.get('conversation_id'),request_id,time.monotonic()+max(0,deadline-time.time()),trusted)
        return ToolCall(name,args,call_id,key),ctx,snapshot

    async def execute(self,name,args,*,context=None,tool_call_id=None,snapshot=None,nonce=None):
        _,active,_=current_tools()
        if snapshot is None and active is None:
            snapshot=await self.refresh()
        call,ctx,snapshot=self.execution_input(name,args,context=context,tool_call_id=tool_call_id,snapshot=snapshot)
        return await self.engine.dispatch(call,ctx,snapshot,nonce=nonce)

    async def confirmed_action(self,session_id,conversation_id,action_id,description,ticket_type):
        from app.tooling.contracts import RejectedCall
        snapshot=self.registry.snapshot(self.policy_loader.load())
        draft={'description':description,'ticket_type':ticket_type}
        call,ctx,snapshot=self.execution_input('create_ticket',draft,
            context={'session_id':session_id,'conversation_id':conversation_id,'request_id':action_id,
                'trusted':{'confirmed_action':True,'ticket_draft':draft}},
            tool_call_id=uuid.uuid5(uuid.NAMESPACE_URL,'action:'+action_id).hex,snapshot=snapshot)
        prepared=self.engine.prepare(call,ctx,snapshot)
        if isinstance(prepared,RejectedCall):
            return await self.engine.finish(call,ctx,prepared.result)
        nonce=self.capabilities.issue(session_id,call.invocation_key,prepared.args_hash,snapshot.policy_hash)
        return await self.engine.invoke(prepared,ctx,nonce=nonce)

    def run_sync(self,call,ctx,snapshot):
        try:
            loop=asyncio.get_running_loop()
        except RuntimeError:
            loop=None
        if self.loop is None or self.loop.is_closed() or loop is self.loop:
            return ToolExecutionResult('同步入口不能阻塞服务事件循环，请使用异步入口。',status='error',outcome='execution_error')
        future=asyncio.run_coroutine_threadsafe(self.engine.dispatch(call,ctx,snapshot),self.loop)
        try:
            return future.result(timeout=max(0.001,ctx.deadline-time.monotonic())+2)
        except Exception:
            future.cancel()
            return ToolExecutionResult('工具执行未完成，请稍后核实。',status='error',outcome='execution_error')

    async def refresh(self):
        async with self._refresh_lock:
            policy=self.policy_loader.load()
            discovered=await self.manager.discover(policy)
            for source in self._sources|set(policy.servers):
                definitions=[]
                for definition in discovered.get(source,[]):
                    async def handler(args,ctx,_source=source,_name=definition.remote_name or definition.name,
                                      _url=policy.servers[source]['url']):
                        return await self.manager.call(_source,_name,args,deadline=ctx.deadline,expected_url=_url)
                    definitions.append(replace(definition,handler=handler))
                self.registry.replace_source(source,definitions)
            self._sources=set(policy.servers)
            return self.registry.snapshot(policy)

    async def aclose(self):
        await self.manager.aclose()
        self.capabilities.clear()
        self.loop=None


