"""One metering boundary for classification, query rewrite, decisions and text."""
from __future__ import annotations

import asyncio
import json
import time

from langchain_core.messages import AIMessage

from app.kb.query import QueryRewrite
from app.tools import get_tools
from app.workflow.budget import BudgetExceeded, reserve_call, settle_call
from app.workflow.contracts import BudgetState, ControlDecision, IntentDecision

RESPOND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "respond_to_user",
        "description": "信息已足够时回答，缺信息时追问；可建议转人工/建工单，但不执行操作。",
        "parameters": ControlDecision.model_json_schema(),
    },
}
RESPOND_SCHEMA["function"]["parameters"]["required"] = ["response_mode"]


class ModelGateway:
    def __init__(self, model):
        self.model = model

    def _kwargs(self, budget, reservation):
        remaining = budget.deadline - time.time()
        if remaining <= 0:
            raise BudgetExceeded("本轮处理时间已用尽")
        return {"max_tokens": reservation.output_cap, "timeout": remaining}

    def _parse(self, response, schema, budget, reservation):
        raw = response.get("raw") if isinstance(response, dict) else None
        # Account even when the structured result is malformed.
        settle_call(budget, reservation, usage=getattr(raw, "usage_metadata", None),
                    output_text=json.dumps(raw.model_dump() if raw else response, ensure_ascii=False, default=str))
        if not isinstance(response, dict) or response.get("parsing_error") is not None:
            raise ValueError("模型结构化结果非法")
        return schema.model_validate(response.get("parsed"))

    async def classify(self, messages, budget):
        schema = IntentDecision
        reservation = reserve_call(budget, messages, schema_text=json.dumps(schema.model_json_schema(), ensure_ascii=False), output_cap=128)
        kwargs = self._kwargs(budget, reservation)
        async with asyncio.timeout(kwargs["timeout"]):
            response = await self.model.with_structured_output(schema, method="function_calling", include_raw=True, **kwargs).ainvoke(messages, **kwargs)
        return self._parse(response, schema, budget, reservation).intent

    def query_adapter(self, budget):
        gateway = self

        class QueryAdapter:
            def invoke(self, messages):
                reservation = reserve_call(budget, messages, schema_text=json.dumps(QueryRewrite.model_json_schema(), ensure_ascii=False), output_cap=512)
                kwargs = gateway._kwargs(budget, reservation)
                response = gateway.model.with_structured_output(QueryRewrite, method="function_calling", include_raw=True, **kwargs).invoke(messages, **kwargs)
                return gateway._parse(response, QueryRewrite, budget, reservation)

        return QueryAdapter()

    async def decide(self, messages, budget):
        preloaded = any(message.additional_kwargs.get('workflow_evidence') for message in messages)
        tools = [tool for tool in get_tools() if tool.name != "create_ticket" and not (preloaded and tool.name == 'query_faq')]
        schemas = [tool.tool_call_schema.model_json_schema() for tool in tools] + [RESPOND_SCHEMA]
        with budget._lock:
            if budget.decision_calls >= budget.max_decisions:
                raise BudgetExceeded("本轮决策次数已达上限")
            reservation = reserve_call(budget, messages, schema_text=json.dumps(schemas, ensure_ascii=False), output_cap=512)
            budget.decision_calls += 1
        kwargs = self._kwargs(budget, reservation)
        async with asyncio.timeout(kwargs["timeout"]):
            response = await self.model.bind_tools([*tools, RESPOND_SCHEMA], parallel_tool_calls=False).ainvoke(messages, **kwargs)
        if not isinstance(response, AIMessage):
            raise ValueError("模型决策消息非法")
        settle_call(budget, reservation, usage=response.usage_metadata, output_text=json.dumps(response.model_dump(), ensure_ascii=False, default=str))
        return response

    async def stream_answer(self, messages, budget):
        # Keep tool schemas visible but provider-side disabled (ch02 DSML finding).
        tools = [tool for tool in get_tools() if tool.name != "create_ticket"]
        schemas = [tool.tool_call_schema.model_json_schema() for tool in tools]
        reservation = reserve_call(budget, messages, schema_text=json.dumps(schemas, ensure_ascii=False), output_cap=2048)
        kwargs = self._kwargs(budget, reservation)
        gathered = None
        async with asyncio.timeout(kwargs["timeout"]):
            async for chunk in self.model.bind_tools(tools, tool_choice="none", parallel_tool_calls=False).astream(messages, **kwargs):
                if getattr(chunk, "tool_call_chunks", None) or getattr(chunk, "tool_calls", None):
                    raise ValueError("回答阶段不允许调用工具")
                gathered = chunk if gathered is None else gathered + chunk
                yield chunk
        settle_call(budget, reservation, usage=getattr(gathered, "usage_metadata", None),
                    output_text=str(gathered.content) if gathered is not None else "")
