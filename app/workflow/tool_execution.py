"""Workflow retrieval adapters execute inside the same trusted tool engine."""
import time
import uuid
from langchain_core.messages import AIMessage,ToolMessage
from app.context.assembly import bounded_observation
from app.config import get_settings
from app.tooling.contracts import RawToolResult


def call_identity(state,label):
    return uuid.uuid5(uuid.NAMESPACE_URL,f"{state['request_id']}:{label}").hex


async def execute_knowledge(deps,state,budget,runner,*,label):
    blocked=budget.tool_calls>=budget.max_tool_calls or time.time()>=budget.deadline
    if not blocked:
        budget.tool_calls+=1
    async def handler(args,ctx):
        result=await runner(args["keyword"])
        content="\n\n".join(f"{i}. {hit.questions}\n→ {hit.answer}" for i,hit in enumerate(result.hits,1))
        if not result.hits:
            content=f"FAQ 库中没有找到与「{args['keyword']}」相关的条目"
        return RawToolResult(content,result,"success" if result.hits else "not_found")
    return await deps.tool_service.execute("query_faq",{"keyword":state["resolved_question"]},
        context={"deadline":budget.deadline,"trusted":{"knowledge_handler":handler,"budget_exhausted":blocked}},
        tool_call_id=call_identity(state,label))


def failure_trace(state,name,args,result,*,label):
    call_id=call_identity(state,label)
    return {"tool_failure":{"outcome":result.outcome,"content":result.content},"retrieval":None,
        "messages":[AIMessage(content="",tool_calls=[{"name":name,"args":args,"id":call_id}]),
        ToolMessage(content=bounded_observation(result.content,settings=get_settings()),tool_call_id=call_id)]}

