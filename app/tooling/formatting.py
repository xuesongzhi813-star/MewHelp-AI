"""Trusted projection precedes both JSON model observations and checkpoint artifacts."""
import json

from app.tooling.contracts import RawToolResult, ToolExecutionResult
from app.tooling.validation import json_text


def _selection(paths):
    tree = {}
    for path in paths:
        parts = path.split(".")
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
    return tree


def _project(value, tree):
    if isinstance(value, list):
        return [_project(item, tree) for item in value]
    if isinstance(value, dict):
        return {key: _project(value[key], children) if children else value[key]
                for key, children in tree.items() if key in value}
    return value


def _translate(value, mappings, prefix=""):
    if isinstance(value, list):
        return [_translate(v, mappings, prefix) for v in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            path = prefix + "." + key if prefix else key
            mapping = mappings.get(path)
            if mapping is not None:
                result[key] = mapping.get(str(item), "未知状态")
            else:
                result[key] = _translate(item, mappings, path)
        return result
    return value


def format_result(raw: RawToolResult, definition, policy) -> ToolExecutionResult:
    content = raw.content
    if definition.source == "builtin" and definition.name == "query_faq":
        return ToolExecutionResult(str(content), raw.artifact,
                                   outcome=raw.outcome)
    if isinstance(content, str):
        if len(content.encode("utf-8")) > 262144:
            raise ValueError("工具返回过大")
        try:
            content = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, TypeError):
            return ToolExecutionResult(content, outcome=raw.outcome)
    encoded = json_text(content)
    if len(encoded.encode("utf-8")) > 262144:
        raise ValueError("工具返回过大")
    if policy.output_fields:
        content = _project(content, _selection(policy.output_fields))
    content = _translate(content, policy.enum_map)
    return ToolExecutionResult(json_text(content), content, outcome=raw.outcome)

