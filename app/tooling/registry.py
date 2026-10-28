"""Atomic source replacement and stable per-turn model-visible snapshots."""
from __future__ import annotations

import hashlib
import threading
from copy import deepcopy
from dataclasses import replace

from app.tooling.contracts import ToolDefinition, ToolSnapshot
from app.tooling.policy import PolicySnapshot
from app.tooling.validation import NAME, json_text, validate_definition

KNOWN_ALIASES = {("logistics", "query_logistics"): "query_logistics",
                 ("aftersales", "query_warranty"): "query_warranty",
                 ("aftersales", "query_return_progress"): "query_return_progress"}


def _copy_definition(definition):
    validate_definition(definition)
    schema = deepcopy(definition.input_schema)
    identity = {"name": definition.name, "source": definition.source, "remote_name": definition.remote_name,
                "description": definition.description, "input_schema": schema}
    return replace(definition, input_schema=schema,
                   definition_hash=hashlib.sha256(json_text(identity).encode()).hexdigest())


class ToolRegistry:
    def __init__(self):
        self._definitions = {}
        self._lock = threading.RLock()

    def register(self, definition: ToolDefinition, *, replace=False) -> None:
        definition = _copy_definition(definition)
        key = (definition.source, definition.remote_name or definition.name)
        with self._lock:
            if key in self._definitions and not replace:
                raise ValueError("工具已注册，替换须显式声明")
            self._definitions[key] = definition

    def unregister(self, source, name) -> None:
        with self._lock:
            self._definitions.pop((source, name), None)

    def replace_source(self, source, definitions) -> None:
        incoming = {}
        for definition in definitions:
            if definition.source != source:
                raise ValueError("工具来源不匹配")
            copy = _copy_definition(definition)
            key = (source, copy.remote_name or copy.name)
            if key in incoming:
                raise ValueError("来源内工具名重复")
            incoming[key] = copy
        if source != "builtin" and len(incoming) > 100:
            raise ValueError("来源工具过多")
        with self._lock:
            self._definitions = {**{key: value for key, value in self._definitions.items() if key[0] != source}, **incoming}

    def resolve(self, source, remote_name) -> ToolDefinition | None:
        with self._lock:
            definition = self._definitions.get((source, remote_name))
            return replace(definition, input_schema=deepcopy(definition.input_schema)) if definition else None

    def snapshot(self, policy: PolicySnapshot) -> ToolSnapshot:
        with self._lock:
            definitions = list(self._definitions.items())
        metadata, schemas, names = [], [], set()
        reserved = {definition.name for _, definition in definitions if definition.source == "builtin"}
        for (source, remote), definition in sorted(definitions):
            rule = policy.rule(source, remote)
            if not rule.enabled:
                continue
            name = (rule.alias or definition.name) if source == "builtin" else (
                rule.alias or KNOWN_ALIASES.get((source, remote)) or f"mcp__{source}__{remote}")
            if not NAME.fullmatch(name) or name in names or (source != "builtin" and name in reserved):
                raise ValueError("模型可见工具名冲突或非法")
            names.add(name)
            parameters = deepcopy(definition.input_schema)
            schemas.append({"type": "function", "function": {"name": name, "description": definition.description,
                                                             "parameters": parameters}})
            metadata.append({"name": name, "source": source, "remote_name": remote,
                             "description": definition.description, "input_schema": deepcopy(parameters),
                             "definition_hash": definition.definition_hash, "effect": rule.effect,
                             "endpoint": policy.servers.get(source, {}).get("url"),
                             "routing_intents": list(rule.routing_intents)})
        if len(json_text(schemas).encode("utf-8")) > 262144:
            raise ValueError("模型工具Schema总量过大")
        identity = {"definitions": metadata, "schemas": schemas, "policy_hash": policy.version}
        version = hashlib.sha256(json_text(identity).encode()).hexdigest()
        return ToolSnapshot(version, policy.version, tuple(metadata), tuple(schemas))

