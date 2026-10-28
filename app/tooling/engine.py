"""One admission, invocation, retry and audit boundary for all business tools."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import replace
from copy import deepcopy
from typing import Protocol

import httpx

from app.tooling.contracts import (
    ExecutionContext, PreparedCall, RawToolResult, RejectedCall, ToolCall, ToolExecutionResult,
)
from app.tooling.formatting import format_result
from app.tooling.validation import json_text, validate_arguments
from app.observability.spans import observed_call

logger = logging.getLogger(__name__)


class AuditSink(Protocol):
    async def write(self, call: ToolCall, ctx: ExecutionContext, result: ToolExecutionResult) -> None: ...


def _hash(args):
    return hashlib.sha256(json_text(args).encode("utf-8")).hexdigest()


def _error(outcome, code=None, detail=""):
    messages = {
        "permission_denied": "权限拒绝：此工具调用未获有效授权。",
        "invalid_arguments": "参数不合法，请核对必填字段、类型及取值后追问用户或重新调用。",
        "execution_error": "工具服务暂时不可用，请如实说明并稍后再试。",
        "timeout": "工具调用超时，暂时无法确认查询结果。",
    }
    return ToolExecutionResult(messages[outcome] + detail, status="error",
                               outcome=outcome, error_code=code or outcome)


def _temporary(exc):
    if isinstance(exc, (ConnectionError, TimeoutError, httpx.ConnectError, httpx.ReadTimeout,
                        httpx.ConnectTimeout, httpx.RemoteProtocolError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {429, 502, 503, 504}


def _audit_context(ctx, source, remote_name):
    return replace(ctx, trusted={**ctx.trusted,
        "audit_source": "builtin" if source == "builtin" else "mcp",
        "audit_server": None if source == "builtin" else source,
        "audit_remote_name": remote_name, "audit_source_resolution": "registered"})


def _literal_ticket_schema(schema):
    return next((item for item in schema.get('oneOf',[]) if 'description' in item.get('properties',{})),schema)


def _argument_schema(definition,ctx):
    schema=definition.input_schema
    if (definition.source=='builtin' and definition.name=='create_ticket'
            and ctx.trusted.get('confirmed_action') is True):
        # The old confirmed buttons accept their complete server-built text.
        # Only the new model-facing draft protocol has the 2000-character cap.
        schema=deepcopy(_literal_ticket_schema(schema))
        schema['properties']['description'].pop('maxLength',None)
    return schema


class ToolEngine:
    def __init__(self, registry, policy_loader, audit_sink, capabilities, *, retry_delay=0.05):
        self.registry = registry
        self.policy_loader = policy_loader
        self.audit_sink = audit_sink
        self.capabilities = capabilities
        self.retry_delay = retry_delay

    def prepare(self, call, ctx, snapshot, *, policy=None):
        if not isinstance(call.name, str):
            return RejectedCall(call, _error("permission_denied", "unknown_tool"))
        metadata = next((d for d in snapshot.definitions if d["name"] == call.name), None)
        policy = policy if policy is not None else self.policy_loader.load()
        if metadata is None:
            names='、'.join(d['name'] for d in snapshot.definitions[:10])
            return RejectedCall(call,replace(_error("permission_denied","unknown_tool"),
                content=f'权限拒绝：工具“{call.name[:64]}”不存在或未授权，可用工具：{names}'))
        source, remote = metadata["source"], metadata["remote_name"]
        definition = self.registry.resolve(source, remote)
        rule = policy.rule(source, remote)
        if (not rule.enabled or definition is None or definition.definition_hash != metadata["definition_hash"]
                or (source != "builtin" and policy.servers.get(source, {}).get("url") != metadata["endpoint"])):
            return RejectedCall(call, _error("permission_denied", "policy_or_definition_changed"))
        if ctx.trusted.get('budget_exhausted'):
            return RejectedCall(call,_error('permission_denied','tool_budget_exhausted'))
        if ctx.trusted.get('preloaded_refund') and rule.effect=='read':
            return RejectedCall(call,_error('permission_denied','preloaded_refund_no_more_queries'))
        if definition.name=='query_faq' and ctx.trusted.get('faq_already_retrieved'):
            return RejectedCall(call,_error('permission_denied','already_retrieved'))
        errors = validate_arguments(_argument_schema(definition,ctx), call.args)
        if errors:
            return RejectedCall(call, _error("invalid_arguments", detail=json_text(errors)))
        resolved_args=None
        if rule.effect == "write":
            if source != "builtin" or definition.name != "create_ticket":
                return RejectedCall(call, _error("permission_denied", "unsupported_write"))
            if (type(ctx.conversation_id) is not int or ctx.conversation_id <= 0
                    or not ctx.session_id or not ctx.request_id):
                return RejectedCall(call, _error("permission_denied", "missing_conversation"))
            draft = ctx.trusted.get("ticket_draft")
            if not (ctx.trusted.get("ticket_requested") is True or ctx.trusted.get("confirmed_action") is True):
                return RejectedCall(call, _error("permission_denied", "ticket_not_requested"))
            if set(call.args)=={'draft_id'}:
                if (ctx.trusted.get('ticket_requested') is not True or not isinstance(draft,dict)
                        or call.args['draft_id']!=ctx.trusted.get('ticket_draft_id')):
                    return RejectedCall(call,_error('permission_denied','unverified_draft_reference'))
                if validate_arguments(_literal_ticket_schema(definition.input_schema),draft):
                    return RejectedCall(call,_error('invalid_arguments','invalid_verified_draft'))
                resolved_args=dict(draft)
            elif draft is not None and call.args != draft:
                return RejectedCall(call, _error("invalid_arguments", "unverified_ticket_content"))
            if not (resolved_args or call.args).get("description", "").strip():
                return RejectedCall(call, _error("invalid_arguments", "missing_description"))
        args_hash=_hash({'arguments':call.args,'resolved':resolved_args}) if resolved_args is not None else _hash(call.args)
        return PreparedCall(call, definition.definition_hash, args_hash, rule.effect == "write",
                            source, remote, metadata.get("endpoint"), policy.version,resolved_args)

    async def reject(self,call,ctx,snapshot,result):
        metadata=next((d for d in snapshot.definitions if d['name']==call.name),None)
        if metadata is not None:
            ctx=_audit_context(ctx,metadata['source'],metadata['remote_name'])
        return await self.finish(call,ctx,result)

    async def finish(self, call, ctx, result):
        try:
            async with asyncio.timeout(1.0):
                await self.audit_sink.write(call, ctx, result)
        except Exception as exc:
            logger.warning("tool audit unavailable invocation=%s exception=%s",
                           call.invocation_key, type(exc).__name__)
        return result

    async def dispatch(self, call, ctx, snapshot, *, nonce=None):
        metadata = next((d for d in snapshot.definitions if d["name"] == call.name), None)
        if metadata is not None:
            ctx = _audit_context(ctx, metadata["source"], metadata["remote_name"])
        prepared = self.prepare(call, ctx, snapshot)
        if isinstance(prepared, RejectedCall):
            return await self.finish(call, ctx, prepared.result)
        return await self.invoke(prepared, ctx, nonce=nonce)

    @observed_call('tool-execution', input_selector=lambda args, kw:
        {'name':args[1].call.name, 'arguments':args[1].resolved_args},
        output_selector=lambda result: {'status':result.status, 'outcome':result.outcome,'content':result.content})
    async def invoke(self, prepared, ctx, *, nonce=None):
        started = time.monotonic()
        ctx = _audit_context(ctx, prepared.source, prepared.remote_name)
        call = prepared.call
        policy = self.policy_loader.load()
        definition = self.registry.resolve(prepared.source, prepared.remote_name)
        rule = policy.rule(prepared.source, prepared.remote_name)
        if (definition is None or not rule.enabled or definition.definition_hash != prepared.definition_hash
                or (prepared.source != "builtin" and policy.servers.get(prepared.source, {}).get("url") != prepared.endpoint)):
            return await self.finish(call, ctx, _error("permission_denied", "policy_or_definition_changed"))
        # Re-validate the full call, not merely the remembered name or nonce.
        metadata = {"name": call.name, "source": prepared.source, "remote_name": prepared.remote_name,
                    "definition_hash": prepared.definition_hash, "endpoint": prepared.endpoint}
        from app.tooling.contracts import ToolSnapshot
        checked = self.prepare(call, ctx, ToolSnapshot("", policy.version, (metadata,), ()),policy=policy)
        if isinstance(checked, RejectedCall):
            return await self.finish(call, ctx, checked.result)
        if (checked.args_hash != prepared.args_hash or checked.requires_confirmation != prepared.requires_confirmation
                or checked.resolved_args!=prepared.resolved_args):
            return await self.finish(call, ctx, _error("permission_denied", "prepared_call_changed"))
        if rule.effect == "write" and not self.capabilities.consume(
            nonce, ctx.session_id, call.invocation_key, prepared.args_hash, policy_hash=policy.version
        ):
            return await self.finish(call, ctx, _error("permission_denied", "confirmation_required"))
        budget = min(rule.timeout or ctx.trusted.get("tool_timeout", 10.0), ctx.deadline - started)
        if budget <= 0:
            return await self.finish(call, ctx, _error("timeout", "deadline_expired"))
        args = dict(checked.resolved_args if checked.resolved_args is not None else call.args)
        if checked.resolved_args is not None:
            ctx=replace(ctx,trusted={**ctx.trusted,'audit_arguments':{**call.args,**checked.resolved_args}})
        if definition.name == "create_ticket" and prepared.source == "builtin":
            args["conversation_id"] = ctx.conversation_id
        retries = 0
        attempts = 2 if rule.effect == "read" else 1
        result = _error("execution_error")
        try:
            async with asyncio.timeout(budget):
                for attempt in range(attempts):
                    if attempt:
                        policy=self.policy_loader.load()
                        checked=self.prepare(call,ctx,ToolSnapshot('',policy.version,(metadata,),()),policy=policy)
                        if isinstance(checked,RejectedCall):
                            result=checked.result
                            break
                        if checked.args_hash!=prepared.args_hash or checked.requires_confirmation!=prepared.requires_confirmation:
                            result=_error('permission_denied','prepared_call_changed')
                            break
                        definition=self.registry.resolve(prepared.source,prepared.remote_name)
                        rule=policy.rule(prepared.source,prepared.remote_name)
                        retries+=1
                    try:
                        if definition.handler is None:
                            raise RuntimeError("handler_unavailable")
                        raw = await definition.handler(args, ctx)
                        if not isinstance(raw, RawToolResult):
                            raise TypeError("handler_contract")
                        if raw.outcome not in {"success", "not_found"}:
                            result = _error("execution_error", "remote_tool_error")
                        else:
                            result = format_result(raw, definition, rule)
                        break
                    except Exception as exc:
                        if attempt + 1 < attempts and _temporary(exc):
                            await asyncio.sleep(self.retry_delay)
                            continue
                        result = _error("timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException))
                                        else "execution_error", "tool_failed")
                        logger.warning("tool failed invocation=%s exception=%s",
                                       call.invocation_key, type(exc).__name__)
                        break
        except TimeoutError:
            result = _error("timeout")
        if result.outcome == "timeout" and rule.effect == "write":
            result = replace(result, content="写操作结果待核实，请联系客服核实，请勿重复提交。")
        result = replace(result, retry_count=retries, elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
        return await self.finish(call, ctx, result)

