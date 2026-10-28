"""MCP sessions live and close on one owner task per local server."""
from __future__ import annotations
import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
import httpx

from app.tooling.contracts import ToolDefinition, RawToolResult
from app.tooling.validation import validate_definition


@asynccontextmanager
async def connect_session(url):
    # Policy only admits loopback HTTP. Avoid Windows environment proxy lookup
    # (and proxy forwarding) consuming the bounded discovery deadline.
    async with httpx.AsyncClient(trust_env=False,timeout=httpx.Timeout(30,read=300)) as client:
        async with streamable_http_client(url,http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                yield session


@dataclass
class _Owner:
    url: str
    ready: asyncio.Future
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=16))
    task: asyncio.Task | None = None


def _retrieve_error(future):
    if not future.cancelled():
        future.exception()


class MCPManager:
    def __init__(self, *, connect=connect_session, discovery_timeout=3.0):
        self.connect = connect
        self.discovery_timeout = discovery_timeout
        self.owners = {}
        self.diagnostics = {}
        self._lock = asyncio.Lock()
        self._discovery_slots = asyncio.Semaphore(4)

    async def start(self):
        return None

    async def _list_tools(self, session, source):
        definitions, cursor, seen = [], None, set()
        for _ in range(20):
            page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
            for tool in page.tools:
                definition = ToolDefinition(tool.name, tool.description, tool.inputSchema, source, tool.name)
                validate_definition(definition)
                definitions.append(definition)
                if len(definitions) > 100:
                    raise ValueError("too_many_tools")
            cursor = page.nextCursor
            if cursor is None:
                if len({tool.name for tool in definitions}) != len(definitions):
                    raise ValueError("duplicate_tools")
                return definitions
            if cursor in seen:
                raise ValueError("repeated_cursor")
            seen.add(cursor)
        raise ValueError("too_many_pages")

    async def _run_owner(self, source, owner):
        future = None
        try:
            async with self.connect(owner.url) as session:
                await session.initialize()
                owner.ready.set_result(None)
                while True:
                    operation, payload, deadline, future = await owner.queue.get()
                    if future.cancelled():
                        continue
                    try:
                        async with asyncio.timeout(max(0, deadline - time.monotonic())):
                            if operation == "list":
                                result = await self._list_tools(session, source)
                            else:
                                name, args = payload
                                result = await session.call_tool(name, args)
                        if not future.done():
                            future.set_result(result)
                    except Exception as exc:
                        if not future.done():
                            future.set_exception(RuntimeError("mcp_tool_rejected") if isinstance(exc, McpError) else exc)
                        if isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError)):
                            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Only sanitized diagnostics leave this owner task.
        finally:
            if future is not None and not future.done():
                future.set_exception(ConnectionError("mcp_unavailable"))
            if not owner.ready.done():
                owner.ready.set_exception(ConnectionError("mcp_unavailable"))
            while not owner.queue.empty():
                *_, future = owner.queue.get_nowait()
                if not future.done():
                    future.set_exception(ConnectionError("mcp_unavailable"))

    async def _close(self, owner):
        if owner.task and not owner.task.done():
            owner.task.cancel()
        if owner.task:
            await asyncio.gather(owner.task, return_exceptions=True)

    async def _get_owner(self, source, url):
        async with self._lock:
            owner = self.owners.get(source)
            if owner is not None and (owner.url != url or owner.task.done()):
                await self._close(owner)
                del self.owners[source]
                owner = None
            if owner is None:
                if len(self.owners) >= 16:
                    raise RuntimeError("too_many_servers")
                owner = _Owner(url, asyncio.get_running_loop().create_future())
                owner.ready.add_done_callback(_retrieve_error)
                owner.task = asyncio.create_task(self._run_owner(source, owner), name="mcp-owner-" + source)
                self.owners[source] = owner
            return owner

    async def _request(self, owner, operation, payload, deadline):
        future = None
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                await asyncio.shield(owner.ready)
                if owner.task.done():
                    raise ConnectionError("mcp_unavailable")
                future = asyncio.get_running_loop().create_future()
                future.add_done_callback(_retrieve_error)
                owner.queue.put_nowait((operation, payload, deadline, future))
                return await asyncio.shield(future)
        finally:
            if future is not None and not future.done():
                future.cancel()

    async def _discover_source(self, source, url):
        async with self._discovery_slots:
            for attempt in range(2):
                try:
                    owner = await self._get_owner(source, url)
                    result = await self._request(owner, "list", None, time.monotonic() + self.discovery_timeout)
                    self.diagnostics.pop(source, None)
                    return source, result
                except Exception as exc:
                    self.diagnostics[source] = type(exc).__name__
                    owner = self.owners.pop(source, None)
                    if owner is not None:
                        await self._close(owner)
                    if attempt == 1 or not isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError)):
                        break
            return source, None

    async def discover(self, policy):
        enabled = {source: value["url"] for source, value in policy.servers.items()
                   if policy.valid and value.get("enabled")}
        for source in set(self.owners) - set(enabled):
            await self._close(self.owners.pop(source))
        values = await asyncio.gather(*(self._discover_source(source, url) for source, url in enabled.items()))
        return {source: definitions for source, definitions in values if definitions is not None}

    async def call(self, source, remote_name, args, *, deadline, expected_url=None):
        owner = self.owners.get(source)
        if owner is None or owner.task.done():
            raise ConnectionError("mcp_unavailable")
        if expected_url is not None and owner.url!=expected_url:
            raise ConnectionError('mcp_endpoint_changed')
        result = await self._request(owner, "call", (remote_name, args), deadline)
        if result.isError:
            return RawToolResult("远端工具返回错误。", outcome="execution_error")
        if result.structuredContent is not None:
            content = result.structuredContent
        else:
            texts = [block.text for block in result.content if isinstance(block, types.TextContent)]
            if not texts:
                raise ValueError("unsupported_mcp_content")
            content = "\n".join(texts)
            if len(content.encode("utf-8")) > 262144:
                raise ValueError("mcp_content_too_large")
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                pass
        return RawToolResult(content, outcome="not_found" if isinstance(content, dict) and content.get("found") is False else "success")

    async def aclose(self):
        owners = list(self.owners.values())
        self.owners.clear()
        await asyncio.gather(*(self._close(owner) for owner in owners))

