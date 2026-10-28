"""Adapt trusted existing synchronous tools to the async handler contract."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from langchain_core.messages import ToolMessage

from app.tooling.contracts import RawToolResult, ToolDefinition

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="builtin-tools")
_SLOTS = threading.BoundedSemaphore(4)


def _invoke(tool, args, ctx):
    try:
        if tool.name=='create_ticket' and ctx.trusted.get('confirmed_action') is True:
            # Engine has validated the trusted legacy command and consumed its
            # one-shot capability before this adapter. Keep the old full text.
            return tool.func(**args)
        if tool.name == 'query_faq' and ctx.trusted.get('query_plan') is not None:
            args={**args,'query_plan':ctx.trusted['query_plan']}
        if tool.response_format == "content_and_artifact":
            return tool.invoke({"type": "tool_call", "name": tool.name,
                                "id": ctx.trusted.get("tool_call_id", "internal"), "args": args})
        return tool.invoke(args)
    finally:
        _SLOTS.release()


def register_builtins(registry) -> None:
    from app import tools

    for tool in (tools.query_order, tools.query_product, tools.query_faq, tools.create_ticket):
        schema = tool.tool_call_schema.model_json_schema()
        schema["additionalProperties"] = False
        if tool.name=='create_ticket':
            schema={'type':'object','oneOf':[schema,{'type':'object',
                'properties':{'draft_id':{'type':'string','pattern':'^[a-f0-9]{32}$',
                    'description':'系统verified_ticket_draft给出的草稿ID，不重写问题描述'}},
                'required':['draft_id'],'additionalProperties':False}]}

        async def handler(args, ctx, _tool=tool):
            if _tool.name == "query_faq" and ctx.trusted.get("knowledge_handler") is not None:
                return await ctx.trusted["knowledge_handler"](args,ctx)
            if not _SLOTS.acquire(blocking=False):
                raise RuntimeError("tool_pool_busy")
            try:
                future = asyncio.get_running_loop().run_in_executor(_POOL, partial(_invoke, _tool, args, ctx))
                future.add_done_callback(lambda f:f.exception() if not f.cancelled() else None)
            except BaseException:
                _SLOTS.release()
                raise
            # A timed-out waiter must not cancel queued work: _invoke owns releasing
            # the slot, including tasks that have not started on the pool yet.
            output = await asyncio.shield(future)
            if isinstance(output, ToolMessage):
                return RawToolResult(output.content, output.artifact,
                                     "success" if output.status == "success" else "execution_error")
            return RawToolResult(output)

        registry.register(ToolDefinition(tool.name, tool.description, schema, "builtin",
                                         remote_name=tool.name, handler=handler))

