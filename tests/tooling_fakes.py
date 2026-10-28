"""Small external-boundary fakes; the real engine owns policy and execution."""
import json
import time
from dataclasses import dataclass
from app.tooling.contracts import ToolDefinition, ExecutionContext, ToolCall, RawToolResult
from app.tooling.registry import ToolRegistry
from app.tooling.policy import PolicyLoader
from app.tooling.capabilities import CapabilityStore
from app.tooling.engine import ToolEngine


class RecordingHandler:
    def __init__(self, outputs=None):
        self.calls=0
        self.arguments=[]
        self.outputs=list(outputs or [RawToolResult({"status":"ok"})])

    async def __call__(self,args,ctx):
        self.calls+=1
        self.arguments.append(dict(args))
        value=self.outputs[min(self.calls-1,len(self.outputs)-1)]
        if isinstance(value,BaseException):
            raise value
        return value


@dataclass
class AuditEvent:
    call: ToolCall
    ctx: ExecutionContext
    result: object


class FakeAuditSink:
    def __init__(self,fail=False):
        self.rows=[]
        self.fail=fail

    async def write(self,call,ctx,result):
        if self.fail:
            raise RuntimeError("private database address")
        self.rows.append(AuditEvent(call,ctx,result))


def make_engine(tmp_path,*,name="lookup",source="builtin",effect="read",outputs=None,extra=None,audit_fail=False):
    path=tmp_path/"policy.json"
    rules={source+"/"+name:{"enabled":True,"effect":effect,**(extra or {})}}
    servers={} if source=="builtin" else {source:{"url":"http://127.0.0.1:8021/mcp","enabled":True}}
    path.write_text(json.dumps({"servers":servers,"rules":rules}),encoding="utf-8")
    policy=PolicyLoader(path)
    handler=RecordingHandler(outputs)
    registry=ToolRegistry()
    schema={"type":"object","properties":{"id":{"type":"string","minLength":1}},"required":["id"],"additionalProperties":False}
    if name=="create_ticket":
        schema={"type":"object","properties":{"description":{"type":"string","minLength":1,"maxLength":2000},
                "ticket_type":{"enum":["售后","投诉","咨询"]}},"required":["description","ticket_type"],"additionalProperties":False}
    registry.register(ToolDefinition(name,"查询演示",schema,source,name,handler=handler))
    audit=FakeAuditSink(audit_fail)
    capabilities=CapabilityStore()
    engine=ToolEngine(registry,policy,audit,capabilities,retry_delay=0)
    ctx=ExecutionContext("s",42,"r",time.monotonic()+5,{})
    snapshot=registry.snapshot(policy.load())
    visible_name=snapshot.schemas[0]["function"]["name"] if snapshot.schemas else name
    call=ToolCall(visible_name,{"id":"1001"},"call1","a"*32)
    return engine,handler,audit,capabilities,ctx,call,snapshot,path

