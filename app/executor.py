"""Compatibility APIs delegate to the same engine; no independent retry or write path."""
import asyncio
import logging
from app.tooling.contracts import ToolExecutionResult
from app.tooling.scope import current_tools

logger=logging.getLogger(__name__)


def _local_service():
    from app.config import get_settings
    from app.tooling.policy import PolicyLoader
    from app.tooling.service import ToolService
    return ToolService(PolicyLoader(get_settings().tool_policy_path))


def execute_tool_result(name,args,*,context=None,tool_call_id=None):
    try:
        service,snapshot,_=current_tools()
        if service is not None:
            call,ctx,snapshot=service.execution_input(name,args,context=context,tool_call_id=tool_call_id,snapshot=snapshot)
            return service.run_sync(call,ctx,snapshot)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return ToolExecutionResult('此同步入口需要在线程中调用，不能阻塞事件循环。',status='error',outcome='execution_error')
        async def standalone():
            service=_local_service()
            await service.start()
            try:
                # No transport discovery without an application lifecycle.
                snapshot=service.registry.snapshot(service.policy_loader.load())
                return await service.execute(name,args,context=context,tool_call_id=tool_call_id,snapshot=snapshot)
            finally:
                await service.aclose()
        return asyncio.run(standalone())
    except Exception as exc:
        logger.warning('tool compatibility error exception=%s',type(exc).__name__)
        return ToolExecutionResult('工具执行失败，请核实参数或稍后再试。',status='error',outcome='execution_error')


def execute_tool(name,args,*,context=None,tool_call_id=None):
    return execute_tool_result(name,args,context=context,tool_call_id=tool_call_id).content
