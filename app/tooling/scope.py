"""Request/task-local services and immutable model-visible tool snapshots."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict
from app.tooling.contracts import ToolSnapshot

_scope = ContextVar("tool_scope",default=(None,None,{}))


def current_tools():
    return _scope.get()


def snapshot_object(value):
    if value is None or isinstance(value,ToolSnapshot):
        return value
    return ToolSnapshot(value["version"],value["policy_hash"],tuple(value["definitions"]),tuple(value["schemas"]))


@contextmanager
def tool_scope(service,snapshot,state=None):
    token=_scope.set((service,snapshot_object(snapshot),state or {}))
    try:
        yield
    finally:
        _scope.reset(token)


def model_settings(settings,snapshot=None):
    snapshot=snapshot_object(snapshot) if snapshot is not None else current_tools()[1]
    if snapshot is not None and any(d.get("effect")=="write" for d in snapshot.definitions):
        return settings.model_copy(update={"agent_decision_max_tokens":max(settings.agent_decision_max_tokens,2600)})
    return settings

