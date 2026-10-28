"""Serializable contracts. Live handlers and permission capabilities stay out of State."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: object
    tool_call_id: str
    invocation_key: str


@dataclass(frozen=True)
class ExecutionContext:
    session_id: str
    conversation_id: int | None
    request_id: str
    deadline: float
    trusted: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RawToolResult:
    content: object
    artifact: object | None = None
    outcome: str = "success"


ToolHandler = Callable[[dict, ExecutionContext], Awaitable[RawToolResult]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    source: str
    remote_name: str | None = None
    definition_hash: str = ""
    handler: ToolHandler | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class ToolSnapshot:
    version: str
    policy_hash: str
    definitions: tuple[dict, ...]
    schemas: tuple[dict, ...]


@dataclass(frozen=True)
class ToolExecutionResult:
    content: str
    artifact: Any = None
    status: str = "success"
    outcome: str = "success"
    error_code: str | None = None
    retry_count: int = 0
    elapsed_ms: int = 0


@dataclass(frozen=True)
class PreparedCall:
    call: ToolCall
    definition_hash: str
    args_hash: str
    requires_confirmation: bool
    source: str = "builtin"
    remote_name: str = ""
    endpoint: str | None = None
    policy_hash: str = ""
    resolved_args: dict | None = None


@dataclass(frozen=True)
class RejectedCall:
    call: ToolCall
    result: ToolExecutionResult

