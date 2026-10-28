"""Strict JSON Schema checks with in-document references only."""
from __future__ import annotations

import json
import re
from itertools import islice
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from referencing import Registry

from app.tooling.contracts import ToolDefinition

NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RESERVED = frozenset({"conversation_id", "confirmed", "grant", "capability", "nonce",
                      "request_id", "session_id", "tool_call_id", "invocation_key", "query_plan"})
MAX_DEFINITION_BYTES = 65536


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _check_json(value: object, depth=0) -> None:
    if depth > 32:
        raise ValueError("JSON结构过深")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON对象键必须为字符串")
        for item in value.values():
            _check_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_json(item, depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("只接受JSON数据")


def _nodes(value, depth=0):
    if depth > 32:
        raise ValueError("Schema结构过深")
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child, depth + 1)


def _check_references(schema: dict) -> None:
    nodes = list(_nodes(schema))
    if len(nodes) > 4096:
        raise ValueError("Schema过于复杂")
    anchors = {node[key] for node in nodes for key in ("$anchor", "$dynamicAnchor") if key in node}
    for node in nodes:
        for key in ("$ref", "$dynamicRef", "$recursiveRef"):
            if key not in node:
                continue
            ref = node[key]
            if not isinstance(ref, str) or not ref.startswith("#"):
                raise ValueError("只允许文档内Schema引用")
            fragment = unquote(ref[1:])
            if fragment and not fragment.startswith("/"):
                if fragment not in anchors:
                    raise ValueError("Schema锚点不存在")
                continue
            target = schema
            try:
                for part in fragment.split("/")[1:]:
                    part = part.replace("~1", "/").replace("~0", "~")
                    target = target[int(part)] if isinstance(target, list) else target[part]
            except (KeyError, IndexError, TypeError, ValueError):
                raise ValueError("Schema引用不存在") from None
            if not isinstance(target, (dict, bool)):
                raise ValueError("Schema引用目标无效")


def validate_schema(schema: dict) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("工具参数顶层必须为object")
    _check_json(schema)
    if len(json_text(schema).encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise ValueError("Schema过大")
    dialect = schema.get("$schema")
    if dialect and dialect.rstrip("#") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("仅支持JSON Schema 2020-12")
    _check_references(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise ValueError("JSON Schema定义无效") from None


def validate_definition(definition: ToolDefinition) -> None:
    if not isinstance(definition.name, str) or not NAME.fullmatch(definition.name):
        raise ValueError("工具名称非法")
    if definition.remote_name is not None and not NAME.fullmatch(definition.remote_name):
        raise ValueError("远端工具名称非法")
    if not isinstance(definition.source, str) or not NAME.fullmatch(definition.source):
        raise ValueError("工具来源非法")
    if not isinstance(definition.description, str) or not definition.description.strip() or len(definition.description) > 2000:
        raise ValueError("工具用途为空或过长")
    validate_schema(definition.input_schema)
    if len(json_text({"name": definition.name, "description": definition.description,
                      "schema": definition.input_schema}).encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise ValueError("工具定义过大")


def validate_arguments(schema: dict, args: object) -> tuple[dict, ...]:
    try:
        _check_json(args)
        if len(json_text(args).encode("utf-8")) > MAX_DEFINITION_BYTES:
            return ({"path": "$", "constraint": "size", "message": "参数过大"},)
        if isinstance(args, dict) and RESERVED.intersection(args):
            return ({"path": "$", "constraint": "reserved", "message": "不得提供系统保留参数"},)
        validate_schema(schema)
        validator = Draft202012Validator(schema, registry=Registry())
        errors = []
        for error in islice(validator.iter_errors(args), 5):
            path = "$" + "".join(f"[{p}]" if isinstance(p, int) else "." + p for p in error.absolute_path)
            errors.append({"path": path, "constraint": str(error.validator),
                           "message": "参数不符合" + str(error.validator) + "约束"})
        return tuple(errors)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return ({"path": "$", "constraint": "json", "message": "参数不是合法有界JSON"},)
    except Exception:
        return ({"path": "$", "constraint": "schema", "message": "参数定义暂不可用"},)

